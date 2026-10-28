/* Aira — Family (#/family): a private place for the household to stay close.
 *
 * Tabs (?tab=): feed (posts, photos, reactions, comments), chat (the family room and one-to-one rooms, live),
 * calendar (events and answers; download it for a phone's calendar), dates (people, birthdays and anniversaries),
 * wishes (everyone's wish list; "I'll get this" is hidden from the person it is for) and board (adults: the public
 * page for relatives without an account, posts waiting for approval, and whether relatives see the wish lists). Everything runs through /api/family/*
 * (aira/suite/family/family_routes.py) as the person using Aira (life.js). Live pages poll with Aira.suite.live.
 * DOM is built with h() and textContent only.
 *
 * Family is a section of the Social app in the Aira frame: its tabs are the left menu's folders, "+ New" (views-
 * social.js) asks with ?new=event (also post and wish) and the page opens that form once, then drops new= from the
 * address; the top search opens an event with ?tab=calendar&event=<id>.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("family", path, opts);
  // Poll while this screen is on the page (Aira.suite.live alone keeps going across Family's tabs, which live in
  // the query, so every visit would add another loop).
  const live = (node, fn, ms) => {
    const stop = S.live(async () => { if (!node.isConnected) { stop(); return; } await fn(); }, ms);
    return stop;
  };

  // Folder glyphs the shared set lacks (added once, never replaced).
  const GLYPHS = {
    faCake: '<svg viewBox="0 0 24 24"><path d="M4 21V13a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v8M3 21h18"/><path d="M4 16c1.5 1 3 1 4 0s2.5-1 4 0 2.5 1 4 0 2.5-1 4 0M12 11V8"/><path d="M12 5.5c.8-.8.8-1.7 0-2.5-.8.8-.8 1.7 0 2.5z"/></svg>',
    faGift: '<svg viewBox="0 0 24 24"><rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v9h14v-9M12 8v13M12 8c-1.5-3-5-3.5-5-1.2C7 8 9 8 12 8zM12 8c1.5-3 5-3.5 5-1.2C17 8 15 8 12 8z"/></svg>',
    faBoard: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 21h8M12 18v3M7 9h6M7 13h10"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;
  // `title` names the page when the tab is open (the bar above already says Social); `about` is the line under it.
  const TABS = [
    { id: "feed", label: "Feed", icon: "news", title: "Family feed", about: "Photos and news only the family sees." },
    { id: "chat", label: "Chat", icon: "social", title: "Family chat", about: "The family room, and one-to-one chats inside the family." },
    { id: "calendar", label: "Calendar", icon: "interviews", title: "Family calendar", about: "Lunches, school plays and trips, and who is going." },
    { id: "dates", label: "People & dates", icon: "faCake", title: "People & dates", about: "The family, and the birthdays and anniversaries to remember." },
    { id: "wishes", label: "Wish lists", icon: "faGift", title: "Wish lists", about: "Gift ideas for everyone, without spoiling the surprise." },
    { id: "board", label: "Board", icon: "faBoard", title: "Family board", about: "A page for relatives without an account, and the messages they send." },
  ];
  // What "+ New" asked for (?new=), answered once the page is drawn.
  let pending = "";
  const NEW_TAB = { post: "feed", event: "calendar", wish: "wishes" };
  const MILESTONE_LABELS = { birthday: "Birthday", new_job: "New job", holiday: "Holiday", baby: "New baby", wedding: "Wedding", graduation: "Graduation", home: "New home", achievement: "Proud moment", other: "Milestone" };
  const REACTION_NAMES = { "❤️": "love", "👍": "like", "😂": "laugh", "😮": "wow", "🎉": "celebrate" };

  const face = (person, size = 40) => h("span", { "aria-hidden": "true", style: { display: "inline-grid", placeItems: "center", width: `${size}px`, height: `${size}px`, borderRadius: `${Math.round(size / 3)}px`, background: (person && person.color) || "#64748b", fontSize: `${Math.round(size * 0.55)}px`, flex: "none" }, text: (person && person.emoji) || "🙂" });
  const when = (iso) => {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    const today = new Date();
    const sameDay = date.toDateString() === today.toDateString();
    return sameDay ? date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : date.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  };
  const toIso = (local) => (local ? new Date(local).toISOString() : "");
  const toLocalInput = (iso) => {
    if (!iso) return "";
    const date = new Date(iso);
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };

  // ---------------------------------------------------------------------------
  // Feed
  // ---------------------------------------------------------------------------
  function postNode(post, ctx) {
    const reactionRow = h("div", { class: "btn-row", role: "group", "aria-label": "Reactions" }, ...ctx.reactions.map((emoji) => {
      const count = post.reactions[emoji] || 0;
      const mine = (post.mine || []).includes(emoji);
      return h("button", { type: "button", class: `btn sm ${mine ? "secondary" : "ghost"}`, "aria-pressed": String(mine), "aria-label": `${REACTION_NAMES[emoji] || "react"}${count ? ` (${count})` : ""}`, onclick: async () => { const result = await api(`/posts/${post.id}/react`, { method: "POST", body: { emoji } }); post.reactions = result.reactions; post.mine = result.mine; ctx.redraw(); } }, h("span", { text: emoji }), count ? h("span", { text: String(count) }) : null);
    }));
    const commentInput = h("input", { type: "text", placeholder: "Write a comment", "aria-label": `Comment on ${post.author.name}'s post`, maxlength: "1000", "data-comment-for": post.id, value: ctx.drafts.get(post.id) || "" });
    commentInput.addEventListener("input", () => { if (commentInput.value) ctx.drafts.set(post.id, commentInput.value); else ctx.drafts.delete(post.id); });
    if (ctx.refocus === post.id) { ctx.refocus = ""; setTimeout(() => { commentInput.focus(); commentInput.setSelectionRange(commentInput.value.length, commentInput.value.length); }, 0); }
    const sendComment = async () => {
      const text = commentInput.value.trim();
      if (!text) return;
      await api(`/posts/${post.id}/comments`, { method: "POST", body: { text } });
      ctx.drafts.delete(post.id);
      ctx.refocus = post.id;
      await ctx.reload();
    };
    commentInput.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); sendComment(); } });
    const menu = h("div", { class: "btn-row" },
      ctx.isAdult ? S.button(post.pinned ? "Unpin" : "Pin to top", async () => { await api(`/posts/${post.id}/pin`, { method: "POST", body: { pinned: !post.pinned } }); await ctx.reload(); }, { kind: "ghost sm" }) : null,
      post.author.id === ctx.me.id && post.source === "member" ? S.button("Edit", () => S.edit({ title: "Edit your post", fields: [{ name: "text", label: "Message", type: "textarea", rows: 5 }], values: post, onSave: async (values) => { await api(`/posts/${post.id}`, { method: "PATCH", body: values }); await ctx.reload(); } }), { kind: "ghost sm" }) : null,
      post.author.id === ctx.me.id || ctx.isAdult ? S.button("Remove", async () => { if (await Aira.confirm({ title: "Remove this post?", message: "It disappears from the feed and the family board.", confirmLabel: "Remove", danger: true })) { await api(`/posts/${post.id}`, { method: "DELETE" }); await ctx.reload(); } }, { kind: "ghost sm" }) : null);
    return h("article", { class: "card suite-section", "aria-label": `Post by ${post.author.name}` },
      h("header", { style: { display: "flex", gap: "12px", alignItems: "center" } }, face(post.author, 44),
        h("div", { style: { flex: "1", minWidth: "0" } }, h("strong", { text: post.author.name }), post.source === "board" ? h("span", { class: "muted small", text: " · from the family board" }) : null, h("div", { class: "muted small", text: when(post.created_at) })),
        post.pinned ? S.pill("pinned", { pinned: "info" }) : null,
        post.milestone ? S.pill(MILESTONE_LABELS[post.milestone] || "milestone", { [MILESTONE_LABELS[post.milestone] || "milestone"]: "good" }) : null),
      post.text ? h("p", { style: { whiteSpace: "pre-wrap", fontSize: "16px", margin: "0" }, text: post.text }) : null,
      post.photos.length ? h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: "8px" } }, ...post.photos.map((src, index) => h("a", { href: src, target: "_blank", rel: "noopener noreferrer" }, h("img", { src, alt: `Photo ${index + 1} from ${post.author.name}`, loading: "lazy", referrerpolicy: "no-referrer", style: { width: "100%", aspectRatio: "4 / 3", objectFit: "cover", borderRadius: "10px", background: "var(--surface-2)" } })))) : null,
      reactionRow,
      post.comments.length ? h("ul", { class: "suite-activity" }, ...post.comments.map((comment) => h("li", {}, face(comment.author, 28), h("div", { style: { flex: "1" } }, h("strong", { text: comment.author.name }), h("span", { text: ` ${comment.text}` }), h("div", { class: "muted small", text: when(comment.created_at) })), comment.author.id === ctx.me.id || ctx.isAdult ? h("button", { type: "button", class: "btn ghost sm", "aria-label": "Remove comment", text: "Remove", onclick: async () => { await api(`/comments/${comment.id}`, { method: "DELETE" }); await ctx.reload(); } }) : null))) : null,
      h("div", { style: { display: "flex", gap: "8px" } }, h("div", { style: { flex: "1" } }, commentInput), S.button("Comment", sendComment, { kind: "secondary sm" })),
      menu);
  }

  async function feedTab(body, overview) {
    const me = overview.me;
    const state = { posts: [], now: "" };
    const list = h("div", { class: "suite-body" });
    // Only posts that changed are rebuilt and the others are not moved, so a comment being typed keeps its text
    // and focus while the feed updates live.
    const built = new Map();
    const emptyNode = Aira.ui.empty("Nothing shared yet", "Share a photo or some news: everyone in the family sees it here.");
    const ctx = {
      me, isAdult: overview.is_adult, reactions: overview.reactions, drafts: new Map(), refocus: "",
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
      reload: async () => { const data = await api("/posts"); state.posts = data.posts; state.now = data.now; ctx.redraw(); },
    };
    const text = h("textarea", { rows: "3", placeholder: `What's new, ${me.name}?`, "aria-label": "Your post", maxlength: "2000", "data-fa-composer": "" });
    const photos = h("textarea", { rows: "2", placeholder: "Photo links (https://…), one per line", "aria-label": "Photo links" });
    const milestone = Aira.ui.select([["", "No milestone"], ...overview.milestones.map((m) => [m, MILESTONE_LABELS[m] || m])], "", { "aria-label": "Milestone" });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const composer = S.section("Share with the family", text, Aira.ui.field("Photos (optional)", photos), Aira.ui.field("Is it a milestone?", milestone), error,
      h("div", { class: "btn-row" }, S.button("Post", async () => {
        error.hidden = true;
        try {
          await api("/posts", { method: "POST", body: { text: text.value, photos: photos.value.split(/\s+/).filter(Boolean), milestone: milestone.value } });
          text.value = ""; photos.value = ""; milestone.value = "";
          await ctx.reload();
          Aira.toast("Shared with the family.", "success");
        } catch (err) { error.textContent = err.message; error.hidden = false; }
      })));
    body.append(...[
      overview.pending ? h("div", { class: "cloud-banner warning", role: "status" }, h("strong", { text: `${overview.pending} post${overview.pending === 1 ? "" : "s"} from the family board waiting for you` }), h("a", { class: "btn secondary sm", href: "#/family?tab=board", text: "Review" })) : null,
      composer, list].filter(Boolean));
    await ctx.reload();
    api("/posts/seen", { method: "POST", body: {}, silent: true }).catch(() => {});
    live(list, async () => {
      const changed = await api(`/posts?since=${encodeURIComponent(state.now)}`, { silent: true });
      if (!changed.posts.length) return;
      state.now = changed.now;
      for (const post of changed.posts) {
        const index = state.posts.findIndex((item) => item.id === post.id);
        if (post.status !== "live") { if (index >= 0) state.posts.splice(index, 1); continue; }
        if (index >= 0) state.posts[index] = post; else state.posts.unshift(post);
      }
      state.posts.sort((a, b) => (b.pinned - a.pinned) || (b.created_at > a.created_at ? 1 : -1));
      ctx.redraw();
      api("/posts/seen", { method: "POST", body: {}, silent: true }).catch(() => {});
    }, 5000);
  }

  // ---------------------------------------------------------------------------
  // Chat
  // ---------------------------------------------------------------------------
  async function chatTab(body, overview, route) {
    const me = overview.me;
    const rooms = await api("/rooms");
    const roomId = route.query.room && rooms.rooms.some((room) => room.id === route.query.room) ? route.query.room : rooms.rooms[0].id;
    const room = rooms.rooms.find((item) => item.id === roomId);
    const others = rooms.people.filter((person) => person.id !== me.id);
    const start = Aira.ui.select([["", "Start a chat with…"], ...others.map((person) => [person.id, `${person.emoji} ${person.name}`])], "", { "aria-label": "Start a chat with" });
    start.addEventListener("change", async () => {
      if (!start.value) return;
      const created = await api("/rooms/direct", { method: "POST", body: { profile_id: start.value } });
      Aira.router.go(["family"], { tab: "chat", room: created.room.id });
    });
    const roomList = h("nav", { "aria-label": "Conversations", style: { display: "grid", gap: "6px", alignContent: "start" } },
      ...rooms.rooms.map((item) => h("a", { href: `#/family?tab=chat&room=${item.id}`, class: `btn ${item.id === roomId ? "secondary" : "ghost"}`, "aria-current": item.id === roomId ? "true" : "false", style: { justifyContent: "flex-start", gap: "10px" } },
        item.kind === "group" ? h("span", { "aria-hidden": "true", text: "👨‍👩‍👧" }) : face(item.other, 28),
        h("span", { style: { flex: "1", textAlign: "left" }, text: item.title }),
        item.unread ? h("span", { class: "count", "aria-label": `${item.unread} unread`, text: String(item.unread), style: { background: "var(--brand)", color: "var(--on-brand)", borderRadius: "999px", padding: "1px 8px", fontSize: "12px" } }) : null)),
      others.length ? start : null);

    const state = { messages: [], now: "", readBy: [] };
    const thread = h("div", { role: "log", "aria-live": "polite", "aria-label": `Messages in ${room.title}`, style: { display: "grid", gap: "10px", alignContent: "start", maxHeight: "60vh", overflowY: "auto", padding: "6px" } });
    // Redrawn only when something changed; it follows new messages only when you are at the bottom, so you can
    // scroll up and read older ones in peace.
    const draw = (follow = false) => {
      const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80;
      const lastMine = [...state.messages].reverse().find((message) => message.mine && !message.deleted);
      const seenBy = lastMine ? state.readBy.filter((reader) => reader.last_read_at >= lastMine.created_at).map((reader) => reader.name) : [];
      thread.replaceChildren(...(state.messages.length ? state.messages.map((message) => {
        const bubble = h("div", { style: { maxWidth: "100%", padding: "10px 14px", borderRadius: "18px", background: message.mine ? "var(--brand)" : "var(--surface-2)", color: message.mine ? "var(--on-brand)" : "var(--ink)", fontSize: "16px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" } },
          message.deleted ? h("em", { text: "Message deleted" }) : null,
          message.text ? h("div", { text: message.text }) : null,
          message.photo ? h("a", { href: message.photo, target: "_blank", rel: "noopener noreferrer" }, h("img", { src: message.photo, alt: `Photo from ${message.author.name}`, loading: "lazy", referrerpolicy: "no-referrer", style: { maxWidth: "100%", borderRadius: "10px", marginTop: "6px" } })) : null);
        return h("div", { style: { display: "flex", gap: "8px", alignItems: "flex-end", justifyContent: message.mine ? "flex-end" : "flex-start" } },
          message.mine ? null : face(message.author, 32),
          h("div", { style: { display: "grid", gap: "2px", maxWidth: "min(80%, 560px)", minWidth: "0", justifyItems: message.mine ? "end" : "start" } },
            message.mine ? null : h("span", { class: "muted small", text: message.author.name }),
            bubble,
            h("span", { class: "muted small" }, h("span", { text: when(message.created_at) }), message.mine && !message.deleted ? h("button", { type: "button", class: "btn ghost sm", text: "Delete", "aria-label": "Delete this message", onclick: async () => { await api(`/messages/${message.id}`, { method: "DELETE" }); await poll(); } }) : null),
            message === lastMine && seenBy.length ? h("span", { class: "muted small", text: `Seen by ${seenBy.join(", ")}` }) : null));
      }) : [Aira.ui.empty("No messages yet", "Say hello!")]));
      if (follow || atBottom) thread.scrollTop = thread.scrollHeight;
    };
    const markRead = () => api(`/rooms/${roomId}/read`, { method: "POST", body: {}, silent: true }).catch(() => {});
    const poll = async (follow = false) => {
      const data = await api(`/rooms/${roomId}/messages?since=${encodeURIComponent(state.now)}`, { silent: true });
      const receipts = JSON.stringify(data.read_by);
      let changed = follow || receipts !== JSON.stringify(state.readBy);
      state.readBy = data.read_by;
      state.now = data.now;
      if (data.messages.length) {
        for (const message of data.messages) {
          const index = state.messages.findIndex((item) => item.id === message.id);
          if (index >= 0) state.messages[index] = message; else state.messages.push(message);
        }
        changed = true;
        markRead();
      }
      if (changed) draw(follow);
    };
    const first = await api(`/rooms/${roomId}/messages`);
    state.messages = first.messages; state.now = first.now; state.readBy = first.read_by;
    draw();
    markRead();

    const input = h("textarea", { rows: "2", placeholder: `Message ${room.kind === "group" ? "the family" : room.title}`, "aria-label": "Your message", maxlength: "2000", style: { fontSize: "16px" } });
    const send = async () => {
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      await api(`/rooms/${roomId}/messages`, { method: "POST", body: { text } });
      input.value = "";
      await poll(true);
      input.focus();
    };
    input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } });
    const photoButton = S.button("Photo link", () => S.edit({ title: "Send a photo", fields: [{ name: "photo", label: "Photo link (https://…)", type: "url", required: true }], onSave: async (values) => { await api(`/rooms/${roomId}/messages`, { method: "POST", body: { photo: values.photo } }); await poll(true); } }), { kind: "ghost" });
    const sendButton = h("button", { type: "button", class: "btn", style: { minHeight: "52px", minWidth: "110px", fontSize: "17px" }, text: "Send", onclick: (event) => Aira.withBusy(event.currentTarget, send) });
    body.append(h("div", { class: "suite-grid", style: { gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))" } },
      S.section("Conversations", roomList),
      h("section", { class: "card suite-section", style: { gridColumn: "span 2" } }, h("h2", { class: "suite-h2", text: room.title }), thread,
        h("div", { style: { display: "flex", gap: "8px", alignItems: "flex-end", flexWrap: "wrap" } }, h("div", { style: { flex: "1 1 240px" } }, input), photoButton, sendButton))));
    thread.scrollTop = thread.scrollHeight;
    live(thread, poll, 2500);
  }

  // ---------------------------------------------------------------------------
  // Calendar
  // ---------------------------------------------------------------------------
  function eventFields(people, values = {}) {
    return [
      { name: "title", label: "What", required: true, placeholder: "Sunday lunch at Grandma's" },
      { name: "all_day", label: "All day", type: "checkbox" },
      { name: "starts_at", label: "Starts", type: "datetime-local", required: true },
      { name: "ends_at", label: "Ends (optional)", type: "datetime-local" },
      { name: "place", label: "Where" },
      { name: "notes", label: "Notes", type: "textarea", rows: 3 },
      { type: "heading", label: "Invite (nobody ticked = everyone)" },
      ...people.map((person) => ({ name: `invite_${person.id}`, label: `${person.emoji} ${person.name}`, type: "checkbox", value: (values.invitees || []).some((p) => p.id === person.id) })),
    ];
  }
  function eventBody(values, people) {
    const invitees = people.filter((person) => values[`invite_${person.id}`]).map((person) => person.id);
    const allDay = !!values.all_day;
    return {
      title: values.title, all_day: allDay, place: values.place, notes: values.notes, invitees,
      starts_at: allDay ? String(values.starts_at || "").slice(0, 10) : toIso(values.starts_at),
      ends_at: allDay ? String(values.ends_at || "").slice(0, 10) : toIso(values.ends_at),
    };
  }

  const addEvent = (people) => S.edit({ title: "Add an event", fields: eventFields(people), submitLabel: "Add the event", onSave: async (values) => { await api("/events", { method: "POST", body: eventBody(values, people) }); Aira.toast("It is on the family calendar.", "success"); Aira.router.render(); } });

  async function calendarTab(body, overview, route) {
    const people = overview.people;
    const data = await api("/events");
    const add = S.button("Add an event", () => addEvent(people), { icon: "plus" });
    const download = data.events.length ? S.button("Add to my phone's calendar", () => Aira.download("/api/family/events.ics", "family-calendar.ics"), { kind: "secondary" }) : null;
    const byDay = new Map();
    for (const event of data.events) {
      const key = new Date(event.starts_at).toDateString();
      if (!byDay.has(key)) byDay.set(key, []);
      byDay.get(key).push(event);
    }
    const answerButton = (event, answer, label) => h("button", { type: "button", class: `btn sm ${event.my_answer === answer ? "" : "secondary"}`, "aria-pressed": String(event.my_answer === answer), text: label, onclick: async (e) => { await Aira.withBusy(e.currentTarget, async () => { await api(`/events/${event.id}/rsvp`, { method: "POST", body: { answer } }); Aira.router.render(); }); } });
    const eventNode = (event) => h("article", { class: `card suite-section ${event.id === route.query.event ? "fa-picked" : ""}`.trim(), dataset: { event: event.id }, tabindex: "-1", "aria-label": event.title },
      h("h3", { style: { margin: 0, fontSize: "18px" }, text: event.title }),
      h("p", { class: "muted", style: { margin: 0 }, text: [event.all_day ? "All day" : `${new Date(event.starts_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}${event.ends_at ? ` – ${new Date(event.ends_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : ""}`, event.place].filter(Boolean).join(" · ") }),
      event.notes ? h("p", { style: { margin: 0, whiteSpace: "pre-wrap" }, text: event.notes }) : null,
      h("p", { class: "small", style: { margin: 0 }, text: `Going: ${event.answers.yes.map((p) => p.name).join(", ") || "nobody yet"}${event.answers.maybe.length ? ` · Maybe: ${event.answers.maybe.map((p) => p.name).join(", ")}` : ""}${event.answers.no.length ? ` · Can't: ${event.answers.no.map((p) => p.name).join(", ")}` : ""}` }),
      h("p", { class: "muted small", style: { margin: 0 }, text: event.everyone ? "Everyone is invited" : `Invited: ${event.invitees.map((p) => p.name).join(", ")}` }),
      h("div", { class: "btn-row", role: "group", "aria-label": "Are you going?" }, answerButton(event, "yes", "Going"), answerButton(event, "maybe", "Maybe"), answerButton(event, "no", "Can't go"),
        event.mine || overview.is_adult ? S.button("Edit", () => S.edit({ title: "Edit event", fields: eventFields(people, event), values: { ...event, starts_at: event.all_day ? event.starts_at.slice(0, 10) : toLocalInput(event.starts_at), ends_at: event.ends_at ? (event.all_day ? event.ends_at.slice(0, 10) : toLocalInput(event.ends_at)) : "" }, onSave: async (values) => { await api(`/events/${event.id}`, { method: "PATCH", body: eventBody(values, people) }); Aira.router.render(); } }), { kind: "ghost sm" }) : null,
        event.mine || overview.is_adult ? S.button("Cancel event", async () => { if (await Aira.confirm({ title: "Cancel this event?", message: "It is removed from everyone's calendar.", confirmLabel: "Cancel event", danger: true })) { await api(`/events/${event.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) : null));
    body.append(h("div", { class: "btn-row" }, add, download),
      ...(byDay.size ? [...byDay.entries()].map(([day, events]) => h("section", { class: "suite-body" }, h("h2", { class: "suite-h2", text: new Date(day).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }) }), ...events.map(eventNode))) : [Aira.ui.empty("Nothing planned", "Add a family lunch, a school play or a trip: everyone can say if they are going.", S.button("Add an event", () => addEvent(people), { kind: "secondary sm", icon: "plus" }))]));
    // An event chosen in the search box: brought into view and focused.
    const picked = route.query.event ? body.querySelector(`[data-event="${CSS.escape(route.query.event)}"]`) : null;
    if (picked) requestAnimationFrame(() => { picked.scrollIntoView({ block: "center" }); picked.focus({ preventScroll: true }); });
  }

  // ---------------------------------------------------------------------------
  // People and dates
  // ---------------------------------------------------------------------------
  async function datesTab(body, overview) {
    const data = await api("/dates");
    const me = overview.me;
    const people = overview.people.filter((person) => person.id !== me.id);
    const message = async (person) => { const created = await api("/rooms/direct", { method: "POST", body: { profile_id: person.id } }); Aira.router.go(["family"], { tab: "chat", room: created.room.id }); };
    body.append(
      S.section("Your family", people.length
        ? h("ul", { class: "life-people" }, ...people.map((person) => h("li", { class: "life-person" }, face(person, 40), h("div", {}, h("strong", { text: person.name }), h("span", { class: "muted small", text: ` ${person.age_group}` })), S.button(`Message ${person.name}`, () => message(person), { kind: "secondary sm" }))))
        : h("p", { class: "muted", text: "Add the people in your household under Display and reading, People on this device." })),
      S.section("Birthdays and anniversaries",
        h("div", { class: "btn-row" }, S.button("Add a date", () => S.edit({ title: "Add a birthday or anniversary", fields: [{ name: "name", label: "Name", required: true, placeholder: "Uncle Raj" }, { name: "kind", label: "What", type: "select", options: [["birthday", "Birthday"], ["anniversary", "Anniversary"], ["other", "Other day to remember"]] }, { name: "date", label: "Date", type: "date", required: true }, { name: "year_known", label: "I know the year", type: "checkbox", value: true }, { name: "relation", label: "Who they are", placeholder: "Uncle, best friend" }], values: { kind: "birthday", year_known: true }, onSave: async (values) => { await api("/dates", { method: "POST", body: values }); Aira.router.render(); } }), { kind: "secondary" })),
        data.dates.length ? h("ul", { class: "life-lines" }, ...data.dates.map((entry) => h("li", { class: "life-line" },
          h("span", { "aria-hidden": "true", style: { fontSize: "26px" }, text: entry.emoji || "🎂" }),
          h("div", { style: { flex: "1" } }, h("span", { class: "life-line-text", text: `${entry.name}'s ${entry.kind === "other" ? "day" : entry.kind}` }), h("span", { class: "muted life-line-sub", text: `${new Date(`${entry.next}T00:00:00`).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })} · ${entry.in_days === 0 ? "today" : entry.in_days === 1 ? "tomorrow" : `in ${entry.in_days} days`}${entry.years ? ` · ${entry.kind === "birthday" ? "turning" : ""} ${entry.years}${entry.kind === "anniversary" ? " years" : ""}` : ""}${entry.relation && entry.relation !== "household" ? ` · ${entry.relation}` : ""}` })),
          entry.profile_id === me.id ? null : S.button("Send wishes", async () => { await api("/wishes", { method: "POST", body: { name: entry.name, kind: entry.kind } }); Aira.toast("Your wishes are on the family feed.", "success"); }, { kind: "secondary sm" }),
          entry.profile_id && entry.profile_id !== me.id && entry.kind === "birthday" && (data.wishes || {})[entry.profile_id] ? h("a", { class: "btn ghost sm", href: "#/family?tab=wishes", text: `Gift ideas (${data.wishes[entry.profile_id]})` }) : null,
          !entry.profile_id && (overview.is_adult || entry.created_by === me.id) ? S.button("Remove", async () => { if (await S.confirmDelete(`${entry.name}'s ${entry.kind}`)) { await api(`/dates/${entry.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) : null))) : h("p", { class: "muted", text: "No dates yet. Add birthdays to the household's profiles, or add relatives here." })));
  }

  // ---------------------------------------------------------------------------
  // Wish lists: gift ideas; who is getting what stays hidden from the person it is for
  // ---------------------------------------------------------------------------
  const WISH_FIELDS = [
    { name: "title", label: "What would you like?", required: true, placeholder: "A blue scarf" },
    { name: "link", label: "Where to find it (https://…, optional)", type: "url" },
    { name: "note", label: "Size, colour or anything else (optional)", type: "textarea", rows: 2 },
  ];
  async function wishesTab(body, overview) {
    const data = await api("/wishlists");
    const open = (list) => list.wishes.filter((wish) => !wish.received).length;
    const wishLine = (wish, list) => h("li", { class: "life-line", style: { flexWrap: "wrap", opacity: wish.received ? "0.65" : "1" } },
      h("span", { "aria-hidden": "true", style: { fontSize: "24px" }, text: wish.received ? "✅" : "🎁" }),
      h("div", { style: { flex: "1 1 200px", minWidth: "0" } },
        /^https:\/\//i.test(wish.link || "") ? h("a", { class: "life-line-text", href: wish.link, target: "_blank", rel: "noopener noreferrer", text: wish.title }) : h("span", { class: "life-line-text", text: wish.title }),
        wish.note ? h("span", { class: "muted life-line-sub", style: { whiteSpace: "pre-wrap" }, text: wish.note }) : null,
        wish.received ? h("span", { class: "muted life-line-sub", text: "Received" }) : null,
        !list.mine && !wish.received && wish.claimed ? h("span", { class: "life-line-sub", text: wish.claimed_by_me ? "You are getting this (it stays a surprise)." : wish.claimed_on_board ? `${wish.claimed_by} said on the family board they are getting this.` : `${wish.claimed_by} is getting this.` }) : null),
      !list.mine && !wish.received && !wish.claimed ? S.button("I'll get this", async () => { await api(`/wishlists/${wish.id}/claim`, { method: "POST", body: {} }); Aira.toast(`Done. ${list.person.name} will not see it.`, "success"); Aira.router.render(); }, { kind: "secondary sm" }) : null,
      wish.can_unclaim && !wish.received ? S.button(wish.claimed_by_me ? "Take it back" : "Free it up again", async () => { await api(`/wishlists/${wish.id}/claim`, { method: "DELETE" }); Aira.toast(wish.claimed_by_me ? "Taken back." : "Someone else can choose it now.", "success"); Aira.router.render(); }, { kind: "ghost sm" }) : null,
      list.can_manage ? S.button(wish.received ? "Not received" : "Got it", async () => { await api(`/wishlists/${wish.id}`, { method: "PATCH", body: { received: !wish.received } }); Aira.router.render(); }, { kind: "ghost sm" }) : null,
      list.can_manage ? S.button("Change", () => S.edit({ title: "Change the wish", fields: WISH_FIELDS, values: wish, onSave: async (values) => { await api(`/wishlists/${wish.id}`, { method: "PATCH", body: values }); Aira.router.render(); } }), { kind: "ghost sm" }) : null,
      list.can_manage ? S.button("Remove", async () => { if (await S.confirmDelete(`“${wish.title}”`)) { await api(`/wishlists/${wish.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) : null);
    body.append(h("p", { class: "muted", text: "Gift ideas for birthdays and holidays. When you say you will get something, everyone else sees it, so nobody buys the same present twice. The person it is for never sees who is getting what." }));
    // Your list, lists with wishes and children's lists you look after; everyone else on one line (a big
    // organization would otherwise show a long column of empty lists).
    const shown = data.lists.filter((list) => list.mine || list.wishes.length || list.can_manage);
    const quiet = data.lists.filter((list) => !shown.includes(list));
    wishesTab.addMine = () => { const mine = data.lists.find((list) => list.mine); return mine ? S.edit({ title: "Add a wish", fields: WISH_FIELDS, onSave: async (values) => { await api("/wishlists", { method: "POST", body: { ...values, owner_id: mine.person.id } }); Aira.router.render(); } }) : null; };
    for (const list of shown) {
      const add = list.can_manage ? S.button(list.mine ? "Add a wish" : `Add to ${list.person.name}'s list`, () => S.edit({ title: list.mine ? "Add a wish" : `Add to ${list.person.name}'s wish list`, fields: WISH_FIELDS, onSave: async (values) => { await api("/wishlists", { method: "POST", body: { ...values, owner_id: list.person.id } }); Aira.router.render(); } }), { kind: list.mine ? "" : "secondary sm" }) : null;
      const ideas = open(list);
      body.append(S.section(list.mine ? "Your wish list" : `${list.person.name}'s wish list`,
        h("div", { style: { display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" } }, face(list.person, 36), h("span", { style: { flex: "1" }, class: "muted", text: ideas ? `${ideas} gift idea${ideas === 1 ? "" : "s"}` : "" }), add),
        list.wishes.length ? h("ul", { class: "life-lines" }, ...list.wishes.map((wish) => wishLine(wish, list))) : h("p", { class: "muted", text: list.mine ? "Nothing yet. Add what you would love to get: the family sees it before your birthday." : `${list.person.name} has not added any wishes yet.` })));
    }
    if (quiet.length) body.append(h("p", { class: "muted", text: `No wishes yet from ${quiet.map((list) => list.person.name).join(", ")}.` }));
  }

  // ---------------------------------------------------------------------------
  // The board (adults)
  // ---------------------------------------------------------------------------
  async function boardTab(body, overview) {
    if (!overview.is_adult) { body.append(Aira.ui.empty("Ask an adult", "The family board is managed by the adults in the household.")); return; }
    const data = await api("/board");
    const title = h("input", { type: "text", value: data.settings.title, maxlength: "60", "aria-label": "Board title" });
    const auto = h("input", { type: "checkbox", checked: data.settings.auto_publish });
    const wishes = h("input", { type: "checkbox", checked: data.settings.show_wishes });
    body.append(
      S.section("The family board", h("p", { class: "muted", text: "A page relatives without an account can open: they see the family feed (never your chats), react, and write a message. You approve their messages first unless you turn that off." }),
        S.share({ title: "Board link", text: "Create a link to send to grandparents, aunts, uncles and friends.", link: data.link, onCreate: async () => (await api("/board/link", { method: "POST", body: {} })).link, onRevoke: async () => { await api("/board/link", { method: "DELETE" }); } }),
        Aira.ui.field("Title", title),
        h("label", { class: "life-toggle" }, auto, h("span", {}, h("strong", { text: "Publish messages from relatives at once" }), h("span", { class: "muted", text: "Off: you approve each one first." }))),
        h("label", { class: "life-toggle" }, wishes, h("span", {}, h("strong", { text: "Show our wish lists on the board" }), h("span", { class: "muted", text: "Relatives see gift ideas and can say they will get one, so nobody buys the same present twice." }))),
        h("div", { class: "btn-row" }, S.button("Save", async () => { await api("/board", { method: "POST", body: { title: title.value, auto_publish: auto.checked, show_wishes: wishes.checked } }); Aira.toast("Saved.", "success"); }))),
      S.section(`Waiting for approval (${data.pending.length})`, data.pending.length ? h("ul", { class: "life-lines" }, ...data.pending.map((post) => h("li", { class: "life-line" },
        h("div", { style: { flex: "1" } }, h("span", { class: "life-line-text", text: post.author.name }), h("span", { class: "life-line-sub", style: { whiteSpace: "pre-wrap" }, text: post.text })),
        S.button("Approve", async () => { await api(`/posts/${post.id}/approve`, { method: "POST", body: {} }); Aira.router.render(); }, { kind: "sm" }),
        S.button("Remove", async () => { await api(`/posts/${post.id}`, { method: "DELETE" }); Aira.router.render(); }, { kind: "ghost sm" })))) : h("p", { class: "muted", text: "Nothing waiting." })));
  }

  Aira.views.family = async (main, route) => {
    // "+ New" asks with ?new=<kind>: remember it, take it out of the address (Back or a refresh must not ask again)
    // and draw the page; the drawn page then opens the form.
    if (route.query.new) {
      pending = String(route.query.new);
      const rest = { ...route.query };
      delete rest.new;
      if (!rest.tab) rest.tab = NEW_TAB[pending] || "";
      Aira.router.go(["family"], rest, { replace: true });
      return;
    }
    let overview;
    try { overview = await api("/overview"); } catch (error) { pending = ""; main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    const unread = overview.unread;
    const tabs = overview.is_adult ? TABS : TABS.filter((tab) => tab.id !== "board");
    const tabId = tabs.some((item) => item.id === route.query.tab) ? route.query.tab : "feed";
    const meta = TABS.find((item) => item.id === tabId);
    const news = [unread.messages ? `${unread.messages} new message${unread.messages === 1 ? "" : "s"}` : "", unread.posts ? `${unread.posts} new post${unread.posts === 1 ? "" : "s"}` : ""].filter(Boolean);
    const body = S.page(main, { title: meta.title, subtitle: (tabId === "feed" || tabId === "chat") && news.length ? news.join(" · ") : meta.about });
    body.classList.add("fa-page");
    document.title = `${meta.title} · Social · Monosage`;
    const tab = S.tabs(body, route, "family", tabs.map(({ id, label, icon }) => ({ id, label, icon })), tabId);
    const panel = h("div", { class: `suite-body ${["feed", "calendar", "dates", "wishes"].includes(tab) ? "so-column" : ""}`.trim() });
    body.append(panel);
    try {
      if (tab === "chat") await chatTab(panel, overview, route);
      else if (tab === "calendar") await calendarTab(panel, overview, route);
      else if (tab === "dates") await datesTab(panel, overview);
      else if (tab === "wishes") await wishesTab(panel, overview);
      else if (tab === "board") await boardTab(panel, overview);
      else await feedTab(panel, overview);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); }
    const ask = pending;
    pending = "";
    if (!ask || !main.isConnected) return;
    if (ask === "event" && tab === "calendar") addEvent(overview.people);
    else if (ask === "wish" && tab === "wishes" && wishesTab.addMine) wishesTab.addMine();
    else if (ask === "post" && tab === "feed") {
      const box = main.querySelector("[data-fa-composer]");
      if (box) { box.scrollIntoView({ block: "center" }); box.focus(); }
    }
  };
})();

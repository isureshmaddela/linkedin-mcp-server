/* Aira — Family (#/family): a private place for the household to stay close.
 *
 * Tabs (?tab=): feed (posts, photos, reactions, comments), chat (the family room and one-to-one rooms, live),
 * calendar (events and answers), dates (people, birthdays and anniversaries) and board (adults: the public page for
 * relatives without an account, and posts waiting for approval). Everything runs through /api/family/*
 * (aira/suite/family/family_routes.py) as the person using Aira (life.js). Live pages poll with Aira.suite.live.
 * DOM is built with h() and textContent only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("family", path, opts);

  const TABS = [
    { id: "feed", label: "Feed" },
    { id: "chat", label: "Chat" },
    { id: "calendar", label: "Calendar" },
    { id: "dates", label: "People & dates" },
    { id: "board", label: "Board" },
  ];
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
    const commentInput = h("input", { type: "text", placeholder: "Write a comment", "aria-label": `Comment on ${post.author.name}'s post`, maxlength: "1000" });
    const sendComment = async () => {
      const text = commentInput.value.trim();
      if (!text) return;
      await api(`/posts/${post.id}/comments`, { method: "POST", body: { text } });
      commentInput.value = "";
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
    const ctx = {
      me, isAdult: overview.is_adult, reactions: overview.reactions,
      redraw: () => list.replaceChildren(...(state.posts.length ? state.posts.map((post) => postNode(post, ctx)) : [Aira.ui.empty("Nothing shared yet", "Share a photo or some news: everyone in the family sees it here.")])),
      reload: async () => { const data = await api("/posts"); state.posts = data.posts; state.now = data.now; ctx.redraw(); },
    };
    const text = h("textarea", { rows: "3", placeholder: `What's new, ${me.name}?`, "aria-label": "Your post", maxlength: "2000" });
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
    S.live(async () => {
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
    const draw = () => {
      const lastMine = [...state.messages].reverse().find((message) => message.mine && !message.deleted);
      const seenBy = lastMine ? state.readBy.filter((reader) => reader.last_read_at >= lastMine.created_at).map((reader) => reader.name) : [];
      thread.replaceChildren(...(state.messages.length ? state.messages.map((message) => {
        const bubble = h("div", { style: { maxWidth: "min(80%, 520px)", padding: "10px 14px", borderRadius: "18px", background: message.mine ? "var(--brand)" : "var(--surface-2)", color: message.mine ? "var(--on-brand)" : "var(--ink)", fontSize: "16px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" } },
          message.deleted ? h("em", { text: "Message deleted" }) : null,
          message.text ? h("div", { text: message.text }) : null,
          message.photo ? h("a", { href: message.photo, target: "_blank", rel: "noopener noreferrer" }, h("img", { src: message.photo, alt: `Photo from ${message.author.name}`, loading: "lazy", referrerpolicy: "no-referrer", style: { maxWidth: "100%", borderRadius: "10px", marginTop: "6px" } })) : null);
        return h("div", { style: { display: "flex", gap: "8px", alignItems: "flex-end", justifyContent: message.mine ? "flex-end" : "flex-start" } },
          message.mine ? null : face(message.author, 32),
          h("div", { style: { display: "grid", gap: "2px", justifyItems: message.mine ? "end" : "start" } },
            message.mine ? null : h("span", { class: "muted small", text: message.author.name }),
            bubble,
            h("span", { class: "muted small" }, h("span", { text: when(message.created_at) }), message.mine && !message.deleted ? h("button", { type: "button", class: "btn ghost sm", text: "Delete", "aria-label": "Delete this message", onclick: async () => { await api(`/messages/${message.id}`, { method: "DELETE" }); await poll(); } }) : null),
            message === lastMine && seenBy.length ? h("span", { class: "muted small", text: `Seen by ${seenBy.join(", ")}` }) : null));
      }) : [Aira.ui.empty("No messages yet", "Say hello!")]));
      thread.scrollTop = thread.scrollHeight;
    };
    const markRead = () => api(`/rooms/${roomId}/read`, { method: "POST", body: {}, silent: true }).catch(() => {});
    const poll = async () => {
      const data = await api(`/rooms/${roomId}/messages?since=${encodeURIComponent(state.now)}`, { silent: true });
      state.readBy = data.read_by;
      state.now = data.now;
      if (data.messages.length) {
        for (const message of data.messages) {
          const index = state.messages.findIndex((item) => item.id === message.id);
          if (index >= 0) state.messages[index] = message; else state.messages.push(message);
        }
        markRead();
      }
      draw();
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
      await poll();
      input.focus();
    };
    input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } });
    const photoButton = S.button("Photo link", () => S.edit({ title: "Send a photo", fields: [{ name: "photo", label: "Photo link (https://…)", type: "url", required: true }], onSave: async (values) => { await api(`/rooms/${roomId}/messages`, { method: "POST", body: { photo: values.photo } }); await poll(); } }), { kind: "ghost" });
    const sendButton = h("button", { type: "button", class: "btn", style: { minHeight: "52px", minWidth: "110px", fontSize: "17px" }, text: "Send", onclick: (event) => Aira.withBusy(event.currentTarget, send) });
    body.append(h("div", { class: "suite-grid", style: { gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))" } },
      S.section("Conversations", roomList),
      h("section", { class: "card suite-section", style: { gridColumn: "span 2" } }, h("h2", { class: "suite-h2", text: room.title }), thread,
        h("div", { style: { display: "flex", gap: "8px", alignItems: "flex-end", flexWrap: "wrap" } }, h("div", { style: { flex: "1 1 240px" } }, input), photoButton, sendButton))));
    S.live(poll, 2500);
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

  async function calendarTab(body, overview) {
    const people = overview.people;
    const data = await api("/events");
    const add = S.button("Add an event", () => S.edit({ title: "Add an event", fields: eventFields(people), onSave: async (values) => { await api("/events", { method: "POST", body: eventBody(values, people) }); Aira.router.render(); } }));
    const byDay = new Map();
    for (const event of data.events) {
      const key = new Date(event.starts_at).toDateString();
      if (!byDay.has(key)) byDay.set(key, []);
      byDay.get(key).push(event);
    }
    const answerButton = (event, answer, label) => h("button", { type: "button", class: `btn sm ${event.my_answer === answer ? "" : "secondary"}`, "aria-pressed": String(event.my_answer === answer), text: label, onclick: async (e) => { await Aira.withBusy(e.currentTarget, async () => { await api(`/events/${event.id}/rsvp`, { method: "POST", body: { answer } }); Aira.router.render(); }); } });
    const eventNode = (event) => h("article", { class: "card suite-section" },
      h("h3", { style: { margin: 0, fontSize: "18px" }, text: event.title }),
      h("p", { class: "muted", style: { margin: 0 }, text: [event.all_day ? "All day" : `${new Date(event.starts_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}${event.ends_at ? ` – ${new Date(event.ends_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : ""}`, event.place].filter(Boolean).join(" · ") }),
      event.notes ? h("p", { style: { margin: 0, whiteSpace: "pre-wrap" }, text: event.notes }) : null,
      h("p", { class: "small", style: { margin: 0 }, text: `Going: ${event.answers.yes.map((p) => p.name).join(", ") || "nobody yet"}${event.answers.maybe.length ? ` · Maybe: ${event.answers.maybe.map((p) => p.name).join(", ")}` : ""}${event.answers.no.length ? ` · Can't: ${event.answers.no.map((p) => p.name).join(", ")}` : ""}` }),
      h("p", { class: "muted small", style: { margin: 0 }, text: event.everyone ? "Everyone is invited" : `Invited: ${event.invitees.map((p) => p.name).join(", ")}` }),
      h("div", { class: "btn-row", role: "group", "aria-label": "Are you going?" }, answerButton(event, "yes", "Going"), answerButton(event, "maybe", "Maybe"), answerButton(event, "no", "Can't go"),
        event.mine || overview.is_adult ? S.button("Edit", () => S.edit({ title: "Edit event", fields: eventFields(people, event), values: { ...event, starts_at: event.all_day ? event.starts_at.slice(0, 10) : toLocalInput(event.starts_at), ends_at: event.ends_at ? (event.all_day ? event.ends_at.slice(0, 10) : toLocalInput(event.ends_at)) : "" }, onSave: async (values) => { await api(`/events/${event.id}`, { method: "PATCH", body: eventBody(values, people) }); Aira.router.render(); } }), { kind: "ghost sm" }) : null,
        event.mine || overview.is_adult ? S.button("Cancel event", async () => { if (await Aira.confirm({ title: "Cancel this event?", message: "It is removed from everyone's calendar.", confirmLabel: "Cancel event", danger: true })) { await api(`/events/${event.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) : null));
    body.append(h("div", { class: "btn-row" }, add),
      ...(byDay.size ? [...byDay.entries()].map(([day, events]) => h("section", { class: "suite-body" }, h("h2", { class: "suite-h2", text: new Date(day).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }) }), ...events.map(eventNode))) : [Aira.ui.empty("Nothing planned", "Add a family lunch, a school play or a trip: everyone can say if they are going.")]));
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
          !entry.profile_id && overview.is_adult ? S.button("Remove", async () => { if (await S.confirmDelete(`${entry.name}'s ${entry.kind}`)) { await api(`/dates/${entry.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) : null))) : h("p", { class: "muted", text: "No dates yet. Add birthdays to the household's profiles, or add relatives here." })));
  }

  // ---------------------------------------------------------------------------
  // The board (adults)
  // ---------------------------------------------------------------------------
  async function boardTab(body, overview) {
    if (!overview.is_adult) { body.append(Aira.ui.empty("Ask an adult", "The family board is managed by the adults in the household.")); return; }
    const data = await api("/board");
    const title = h("input", { type: "text", value: data.settings.title, maxlength: "60", "aria-label": "Board title" });
    const auto = h("input", { type: "checkbox", checked: data.settings.auto_publish });
    body.append(
      S.section("The family board", h("p", { class: "muted", text: "A page relatives without an account can open: they see the family feed (never your chats), react, and write a message. You approve their messages first unless you turn that off." }),
        S.share({ title: "Board link", text: "Create a link to send to grandparents, aunts, uncles and friends.", link: data.link, onCreate: async () => (await api("/board/link", { method: "POST", body: {} })).link, onRevoke: async () => { await api("/board/link", { method: "DELETE" }); } }),
        Aira.ui.field("Title", title),
        h("label", { class: "life-toggle" }, auto, h("span", {}, h("strong", { text: "Publish messages from relatives at once" }), h("span", { class: "muted", text: "Off: you approve each one first." }))),
        h("div", { class: "btn-row" }, S.button("Save", async () => { await api("/board", { method: "POST", body: { title: title.value, auto_publish: auto.checked } }); Aira.toast("Saved.", "success"); }))),
      S.section(`Waiting for approval (${data.pending.length})`, data.pending.length ? h("ul", { class: "life-lines" }, ...data.pending.map((post) => h("li", { class: "life-line" },
        h("div", { style: { flex: "1" } }, h("span", { class: "life-line-text", text: post.author.name }), h("span", { class: "life-line-sub", style: { whiteSpace: "pre-wrap" }, text: post.text })),
        S.button("Approve", async () => { await api(`/posts/${post.id}/approve`, { method: "POST", body: {} }); Aira.router.render(); }, { kind: "sm" }),
        S.button("Remove", async () => { await api(`/posts/${post.id}`, { method: "DELETE" }); Aira.router.render(); }, { kind: "ghost sm" })))) : h("p", { class: "muted", text: "Nothing waiting." })));
  }

  Aira.views.family = async (main, route) => {
    let overview;
    try { overview = await api("/overview"); } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    const unread = overview.unread;
    const body = S.page(main, { title: "Family", subtitle: unread.messages || unread.posts ? `${unread.messages} new message${unread.messages === 1 ? "" : "s"} · ${unread.posts} new post${unread.posts === 1 ? "" : "s"}` : "Stay close to the people you love." });
    const tabs = overview.is_adult ? TABS : TABS.filter((tab) => tab.id !== "board");
    const tab = S.tabs(body, route, "family", tabs, tabs.some((item) => item.id === route.query.tab) ? route.query.tab : "feed");
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    try {
      if (tab === "chat") await chatTab(panel, overview, route);
      else if (tab === "calendar") await calendarTab(panel, overview);
      else if (tab === "dates") await datesTab(panel, overview);
      else if (tab === "board") await boardTab(panel, overview);
      else await feedTab(panel, overview);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); }
  };
})();

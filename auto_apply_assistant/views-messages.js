/* Aira — Messages: a two-pane messenger backed by Aira's own copy of your inbox */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, withBusy } = Aira;
  const enc = encodeURIComponent;

  const STATUS_LABELS = {
    sent: ["Replied", "stage applied"],
    held: ["Needs your review", "stage needs_review"],
    failed: ["Send failed", "stage failed"],
  };

  function modeBadge(mode) {
    if (mode === "auto") return h("span", { class: "badge easy", text: "Auto-reply" });
    if (mode === "auto_recruiter") return h("span", { class: "badge", text: "Auto if recruiter" });
    return h("span", { class: "badge", text: "Manual" });
  }

  const OUTCOME_BADGES = {
    sent: "stage applied",
    held: "stage needs_review",
    failed: "stage failed",
    drafted: "stage saved",
  };
  // [id, label on the button, the same scope in a sentence]
  const SCOPES = [
    ["selected", "Conversations I switch on", "conversations you switch on"],
    ["recruiters", "All recruiter conversations", "all recruiter conversations"],
    ["all", "Every conversation", "every conversation"],
  ];
  const FILTERS = [
    ["all", "All"],
    ["needs_reply", "Needs reply"],
    ["recruiters", "Recruiters"],
    ["auto", "Auto-reply"],
  ];

  function lastCheckTable(check, onReview) {
    if (!check || !check.items.length) {
      return h("p", { class: "muted", style: { margin: "10px 0 0", fontSize: "12.5px" }, text: "No inbox check has run yet. Use Check inbox now to see how Monosage handles each conversation." });
    }
    const rows = check.items.map((item) => {
      const open = h("button", { type: "button", class: "btn ghost sm", text: "Open" });
      open.addEventListener("click", () => onReview({ thread_id: item.thread_id, participant: item.participant }));
      const who = item.speaker === "them" ? "They did" : item.speaker === "us" ? "You did" : "Unknown";
      return h("tr", {},
        h("td", {}, h("strong", { text: item.participant || "Conversation" })),
        h("td", {}, item.mode ? modeBadge(item.mode) : h("span", { class: "muted", text: "—" }), item.mode_reason ? h("div", { class: "muted", style: { fontSize: "12px" }, text: item.mode_reason }) : null),
        h("td", { text: item.outcome === "unchanged" ? "—" : who }),
        h("td", {}, h("span", { class: `badge ${OUTCOME_BADGES[item.outcome] || ""}`, text: item.outcome_label }), item.detail ? h("div", { class: "muted", style: { fontSize: "12px", maxWidth: "420px" }, text: item.detail }) : null),
        h("td", {}, open));
    });
    return h("details", { style: { marginTop: "12px" }, open: true },
      h("summary", { text: `Last inbox check · ${fmt.datetime(check.started_at)} · ${fmt.plural(check.items.length, "conversation")}` }),
      h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", { text: "Conversation" }), h("th", { text: "Reply mode and why" }), h("th", { text: "Wrote last" }), h("th", { text: "What Monosage did" }), h("th", { text: "" }))),
        h("tbody", {}, ...rows))));
  }

  function statusBadge(status) {
    const entry = STATUS_LABELS[status];
    return entry ? h("span", { class: `badge ${entry[1]}`, text: entry[0] }) : null;
  }

  // -------------------------------------------------------------------------
  // Automatic replies status panel
  // -------------------------------------------------------------------------
  function scheduleText(status) {
    const schedule = status.schedule;
    if (!schedule) return "Inbox checks are off, so automatic replies cannot run yet.";
    if (!schedule.enabled) return "Your inbox check schedule is paused.";
    const every = schedule.interval_minutes >= 60 ? `${schedule.interval_minutes / 60} h` : `${schedule.interval_minutes} min`;
    return `Inbox checks run every ${every}; next check ${fmt.datetime(schedule.next_run_at)}.`;
  }

  // Refresh the panel every 10 seconds for 3 minutes after an inbox check,
  // stopping early when the user leaves the Messages tab.
  let statusWatch = null;
  function watchStatus(container, onReview) {
    if (statusWatch) clearInterval(statusWatch);
    let ticks = 0;
    statusWatch = setInterval(() => {
      ticks += 1;
      if (!document.body.contains(container) || ticks > 18) {
        clearInterval(statusWatch);
        statusWatch = null;
        return;
      }
      renderStatusPanel(container, onReview);
    }, 10000);
  }

  async function renderStatusPanel(container, onReview) {
    let status;
    try { status = await api("/api/messages/auto-reply", { silent: true }); }
    catch (error) { container.replaceChildren(ui.error(error)); return; }
    container.dispatchEvent(new CustomEvent("autoreply-status", { detail: status }));

    const pauseButton = h("button", { type: "button", class: "btn secondary sm", text: status.paused ? "Resume automatic replies" : "Pause automatic replies" });
    pauseButton.addEventListener("click", () => withBusy(pauseButton, async () => {
      const data = await api("/api/settings", { method: "POST", body: { auto_reply_paused: !status.paused } });
      Aira.state.settings = data.settings;
      toast(status.paused ? "Automatic replies resumed." : "Automatic replies paused. Drafts are still prepared.", "success");
      renderStatusPanel(container, onReview);
    }));

    const runButton = h("button", { type: "button", class: "btn secondary sm", text: "Check inbox now" });
    runButton.addEventListener("click", () => withBusy(runButton, async () => {
      await api("/api/messages/auto-reply/run", { method: "POST", body: {} });
      toast("Checking your inbox. Drafting can take a minute or two; this panel updates as results arrive.", "success");
      watchStatus(container, onReview);
    }));

    const scheduleButton = !status.schedule || !status.schedule.enabled
      ? h("button", { type: "button", class: "btn sm", text: status.schedule ? "Resume inbox checks" : "Check inbox every 30 minutes" })
      : null;
    if (scheduleButton) {
      scheduleButton.addEventListener("click", () => withBusy(scheduleButton, async () => {
        if (status.schedule) await api(`/api/schedules/${status.schedule.id}`, { method: "PATCH", body: { enabled: true } });
        else await api("/api/schedules", { method: "POST", body: { kind: "messages", interval_minutes: 30, max_items: 5 } });
        toast("Inbox checks are on. The first check runs in about a minute.", "success");
        renderStatusPanel(container, onReview);
      }));
    }

    const scopePhrase = (SCOPES.find(([id]) => id === status.scope) || SCOPES[0])[2];
    const summary = `${status.sent_today} of ${status.daily_limit} automatic replies sent today · Auto-reply covers: ${scopePhrase}${status.auto_threads ? ` (+${status.auto_threads} switched on)` : ""}`;
    const scopeGroup = h("div", { class: "subnav", role: "group", "aria-label": "Automatic replies apply to", style: { margin: "0" } });
    for (const [id, label, phrase] of SCOPES) {
      const option = h("button", { type: "button", "aria-pressed": status.scope === id ? "true" : "false", text: label });
      option.addEventListener("click", async () => {
        if (status.scope === id) return;
        if (id !== "selected") {
          const ok = await Aira.confirm({
            title: id === "all" ? "Auto-reply to every conversation?" : "Auto-reply to all recruiter conversations?",
            message: (id === "all" ? "Monosage will reply automatically in every conversation where the other person wrote last. " : "Monosage will reply automatically in conversations whose messages read like recruiter outreach (hiring, roles, resume, W2/C2C, staffing). ")
              + "Every reply still passes the safety checks; anything about pay, visa, personal details, offers, or relocation is held for you. Conversations you set to Manual stay manual.",
            confirmLabel: "Turn on",
          });
          if (!ok) return;
        }
        await withBusy(option, async () => {
          const data = await api("/api/settings", { method: "POST", body: { auto_reply_scope: id } });
          Aira.state.settings = data.settings;
          toast(`Automatic replies now cover: ${phrase}. Run Check inbox now to see each decision.`, "success");
          renderStatusPanel(container, onReview);
        });
      });
      scopeGroup.append(option);
    }
    if (status.running && !statusWatch) watchStatus(container, onReview);
    const recent = (status.recent || []).length
      ? h("details", { style: { marginTop: "12px" }, open: true },
          h("summary", { text: `Recent replies (${status.recent.length})` }),
          h("div", { class: "list" }, ...status.recent.map((item) => {
            const openButton = h("button", { type: "button", class: "btn ghost sm", text: "Open" });
            openButton.addEventListener("click", () => onReview({ thread_id: item.thread_id, participant: item.participant }));
            return h("div", { class: "row" },
              h("div", { style: { minWidth: "0" } },
                h("div", { class: "meta", style: { marginTop: "0" } },
                  h("strong", { text: item.participant || "Conversation" }),
                  statusBadge(item.status) || h("span", { class: "badge", text: item.status }),
                  h("span", { class: "badge", text: item.mode === "auto" ? "Automatic" : "Sent by you" })),
                h("span", { class: "sub", text: item.reply.length > 160 ? `${item.reply.slice(0, 160)}…` : item.reply }),
                item.status !== "sent" && item.detail ? h("span", { class: "sub", text: item.detail }) : null,
                h("small", { class: "muted", text: fmt.datetime(item.created_at) })),
              h("div", { class: "aside" }, openButton));
          })))
      : h("p", { class: "muted", style: { margin: "10px 0 0", fontSize: "12.5px" }, text: "No replies sent or held yet. Sent and held replies appear here and under each conversation." });
    const held = status.held.length
      ? h("div", { class: "list", style: { marginTop: "10px" } }, ...status.held.map((item) => {
          const review = h("button", { type: "button", class: "btn secondary sm", text: "Review" });
          review.addEventListener("click", () => onReview({ thread_id: item.thread_id, participant: item.participant }));
          return h("div", { class: "row" },
            h("div", {}, h("span", { class: "title", text: item.participant || "Conversation" }), h("span", { class: "sub", text: item.detail }), h("small", { class: "muted", text: fmt.ago(item.created_at) })),
            h("div", { class: "aside" }, review));
        }))
      : null;

    container.replaceChildren(
      h("div", { class: "msgr-auto-body" },
        h("div", { class: "btn-row", style: { justifyContent: "space-between", alignItems: "flex-start" } },
          h("div", { style: { minWidth: "0", flex: "1 1 320px" } },
            h("h3", {}, "Automatic replies ", status.paused ? h("span", { class: "badge stage needs_review", text: "Paused" }) : null, status.running ? h("span", { class: "badge stage saved", text: "Inbox check running…" }) : null),
            h("p", { class: "muted", style: { margin: "0 0 4px" }, text: summary }),
            h("p", { class: "muted", style: { margin: "0", fontSize: "12.5px" }, text: scheduleText(status) })),
          h("div", { class: "btn-row" }, scheduleButton, runButton, pauseButton)),
        h("div", { class: "btn-row", style: { marginTop: "12px", alignItems: "center" } },
          h("span", { class: "label", style: { fontSize: "12px", fontWeight: "700" }, text: "Automatic replies apply to" }), scopeGroup),
        lastCheckTable(status.last_check, onReview),
        status.held.length ? h("p", { class: "note warn", style: { marginTop: "12px" }, text: `${fmt.plural(status.held.length, "reply")} held for your review. Monosage never sends replies about pay, visa or work authorization, personal details, offers, relocation, links, or contact details on its own.` }) : null,
        held,
        recent,
      ),
    );
  }

  // -------------------------------------------------------------------------
  // Formatting helpers
  // -------------------------------------------------------------------------
  // The server sends local times as "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM" without a
  // zone. Building the Date from its parts keeps them in local time.
  function parseLocal(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?$/.exec(value || "");
    if (!m) return null;
    return { date: new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)), hasTime: m[4] !== undefined };
  }
  function daysAgo(date) {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const day = new Date(date); day.setHours(0, 0, 0, 0);
    return Math.round((today - day) / 86400000);
  }
  const clock = (date) => date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  function shortDay(date) {
    const diff = daysAgo(date);
    if (diff === 0) return "Today";
    if (diff === 1) return "Yesterday";
    if (diff > 1 && diff < 7) return date.toLocaleDateString([], { weekday: "long" });
    return date.toLocaleDateString([], { month: "short", day: "numeric", year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
  }
  function listTime(conversation) {
    const parsed = parseLocal(conversation.last_message_at);
    if (!parsed) return conversation.last_message_label || "";
    const diff = daysAgo(parsed.date);
    if (diff <= 0 && parsed.hasTime) return clock(parsed.date);
    if (diff === 1) return "Yesterday";
    if (diff > 1 && diff < 7) return parsed.date.toLocaleDateString([], { weekday: "short" });
    return parsed.date.toLocaleDateString([], { month: "short", day: "numeric", year: parsed.date.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
  }
  function initials(name) {
    const parts = String(name || "").replace(/[^\p{L}\p{N}\s'-]/gu, " ").split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    const first = Array.from(parts[0])[0] || "";
    const last = parts.length > 1 ? Array.from(parts[parts.length - 1])[0] || "" : "";
    return (first + last).toUpperCase();
  }
  function hueOf(name) {
    let value = 0;
    for (const ch of String(name || "")) value = (value * 31 + ch.codePointAt(0)) % 360;
    return value;
  }
  function avatar(name, large) {
    const node = h("span", { class: `msgr-avatar${large ? " lg" : ""}`, "aria-hidden": "true", text: initials(name) });
    node.style.setProperty("--h", String(hueOf(name)));
    return node;
  }
  function syncedText(seconds) {
    return seconds ? `Synced ${fmt.ago(seconds)}` : "Not synced yet";
  }

  // -------------------------------------------------------------------------
  // Messages tab
  // -------------------------------------------------------------------------
  let messengerTimer = null;

  Aira.renderMessages = async (content, route) => {
    if (messengerTimer) { clearInterval(messengerTimer); messengerTimer = null; }
    const ctx = {
      filter: FILTERS.some(([id]) => id === Aira.recall("messages.filter", "all")) ? Aira.recall("messages.filter", "all") : "all",
      q: "",
      list: null,
      listToken: 0,
      selected: null,
      thread: null,
      token: 0,
      polling: null,
      drafts: {},
      pushed: false,
    };

    const autoBox = h("div", {});
    const autoSummary = h("span", { class: "msgr-auto-state", text: "" });
    const autoPanel = h("details", { class: "msgr-auto" },
      h("summary", {}, h("strong", { text: "Automatic replies" }), autoSummary),
      autoBox);
    let checkWasRunning = false;
    autoBox.addEventListener("autoreply-status", (event) => {
      const status = event.detail;
      // An inbox check saves what it reads: show it once the check ends.
      if (checkWasRunning && !status.running) { loadList({ silent: true }); reloadOpenThread(); }
      checkWasRunning = !!status.running;
      const parts = [status.paused ? "Paused" : `${status.sent_today} of ${status.daily_limit} sent today`];
      if (status.held.length) parts.push(`${status.held.length} held for review`);
      if (status.running) parts.push("checking inbox");
      autoSummary.textContent = parts.join(" · ");
    });

    // List pane -------------------------------------------------------------
    const syncBar = h("div", { class: "msgr-sync", role: "status", "aria-live": "polite" });
    const syncButton = h("button", { type: "button", class: "btn secondary sm msgr-sync-btn", "aria-label": "Sync inbox now", title: "Refresh the list and read your newest conversations in the background. LinkedIn marks the conversations Monosage opens as read." },
      h("span", { "aria-hidden": "true", text: "↻" }), h("span", { text: "Sync" }));
    const search = h("input", { type: "search", "data-search": "1", placeholder: "Search people and messages", "aria-label": "Search conversations", autocomplete: "off", maxlength: "100" });
    const chips = h("div", { class: "msgr-chips", role: "group", "aria-label": "Filter conversations" });
    const banner = h("div", {});
    const rows = h("div", { class: "msgr-rows", "data-roving": "1", role: "list", "aria-label": "Conversations" });
    const side = h("aside", { class: "msgr-side", "aria-label": "Conversations" },
      h("div", { class: "msgr-side-head" },
        h("div", { class: "msgr-title-row" }, h("h2", { text: "Messages" }), syncButton),
        syncBar,
        h("div", { class: "msgr-search" }, search),
        chips),
      banner, rows);

    // Thread pane -----------------------------------------------------------
    const threadPane = h("section", { class: "msgr-main", "aria-label": "Conversation" });
    const messenger = h("div", { class: "messenger", "data-view": "list" }, side, threadPane);

    content.replaceChildren(autoPanel, messenger);

    // The messenger needs the height: shrink the page heading above it.
    const pageHead = content.parentElement && content.parentElement.querySelector(".page-head");
    if (pageHead) pageHead.classList.add("compact");

    const setView = (view) => messenger.setAttribute("data-view", view);
    const threadHash = (threadId) => {
      const query = new URLSearchParams({ tab: "messages" });
      if (threadId) query.set("thread", threadId);
      return `#/recruiters?${query.toString()}`;
    };
    // Deep links stay inside Aira. Going from the list to a thread adds a history
    // entry, so the browser's Back button returns to the list; moving between
    // threads replaces it. Neither rebuilds the page (see Aira.onHashChange).
    const setThreadParam = (threadId, { push = false } = {}) => {
      const target = threadHash(threadId);
      if (location.hash === target) return;
      try {
        if (push) { history.pushState(null, "", target); ctx.pushed = true; }
        else history.replaceState(null, "", target);
      } catch { /* history unavailable */ }
    };
    // Back and Forward change the thread in place; any other navigation is the router's.
    Aira.onHashChange = () => {
      if (!document.body.contains(messenger)) { Aira.onHashChange = null; return true; }
      const route = Aira.router.parse();
      if (route.path[0] !== "recruiters" || route.path.length > 1 || (route.query.tab || "") !== "messages") return true;
      const wanted = route.query.thread || "";
      const current = ctx.selected ? ctx.selected.thread_id : "";
      if (wanted === current) return false;
      if (wanted) {
        // Forward to a thread: the list entry it was opened from sits below it.
        openThread({ thread_id: wanted, participant: route.query.participant || "" }, { navigate: false });
        ctx.pushed = true;
      } else closeThread({ navigate: false });
      return false;
    };

    const onReview = (conversation) => openThread(conversation);
    renderStatusPanel(autoBox, onReview);

    // Sync progress ---------------------------------------------------------
    function renderSyncBar() {
      const list = ctx.list;
      const sync = list ? list.sync : null;
      syncBar.replaceChildren();
      const running = !!(sync && sync.running);
      syncButton.disabled = running;
      if (running) {
        const percent = sync.total ? Math.round(((sync.done + sync.failed) / sync.total) * 100) : 8;
        const label = sync.phase === "threads" && sync.total ? `Syncing ${sync.done + sync.failed} of ${sync.total} conversations…` : "Syncing your inbox…";
        syncBar.append(h("span", { text: label }), h("span", { class: "msgr-progress", "aria-hidden": "true" }, h("i", { style: { width: `${percent}%` } })));
        return;
      }
      const notes = [];
      if (list && list.synced_at) notes.push(`Synced ${fmt.ago(list.synced_at)}`);
      else notes.push("Not synced yet");
      syncBar.append(h("span", { text: notes.join(" · ") }));
      if (list && list.saved_total) syncBar.append(clearButton);
      if (sync && sync.finished_at && (sync.status === "error" || sync.status === "partial")) {
        syncBar.append(h("span", { class: "msgr-sync-warn", title: sync.error || "", text: sync.status === "partial" ? "Some conversations could not be refreshed." : `Last sync failed${sync.error ? `: ${sync.error}` : "."}` }));
      }
    }

    // Saved copies of private messages can be deleted at any time.
    const clearButton = h("button", { type: "button", class: "msgr-link", title: "Delete the conversations and messages Monosage saved on this computer", text: "Clear saved copy" });
    clearButton.addEventListener("click", async () => {
      const ok = await Aira.confirm({
        title: "Clear the saved copy of your messages?",
        message: "Monosage deletes the conversations and messages it saved on this computer. Nothing is deleted on LinkedIn, and a sync saves them again while LinkedIn is connected. Drafts, sent-reply history and recruiter records are not affected.",
        confirmLabel: "Clear saved copy",
        danger: true,
      });
      if (!ok) return;
      try {
        const result = await api("/api/inbox/cache", { method: "DELETE" });
        toast(`Cleared ${fmt.plural(result.threads, "conversation")} and ${fmt.plural(result.messages, "message")}.`, "success");
        ctx.list = null;
        if (ctx.selected) closeThread();
        await loadList();
      } catch (error) { Aira.reportError(error); }
    });

    function stopPolling() {
      if (ctx.polling) { clearInterval(ctx.polling); ctx.polling = null; }
    }
    function pollSync() {
      if (ctx.polling) return;
      ctx.polling = setInterval(async () => {
        if (!document.body.contains(messenger)) { stopPolling(); return; }
        let sync;
        try { sync = await api("/api/inbox/sync", { silent: true }); } catch { return; }
        if (ctx.list) ctx.list.sync = sync;
        renderSyncBar();
        if (sync.running) {
          // Show conversations as soon as the list is saved, then as each thread lands.
          const progress = `${sync.phase}:${sync.done}:${sync.failed}`;
          if (progress !== ctx.progress) {
            ctx.progress = progress;
            loadList({ silent: true });
          }
          return;
        }
        ctx.progress = undefined;
        stopPolling();
        await loadList({ silent: true });
        if (sync.status === "error") toast(`Inbox sync failed${sync.error ? `: ${sync.error}` : "."} Showing your saved conversations.`, "error", { timeout: 8000 });
        await reloadOpenThread();
      }, 1500);
    }

    async function startSync({ manual }) {
      let result;
      try { result = await api("/api/inbox/sync", { method: "POST", body: manual ? { trigger: "manual" } : { trigger: "tab", only_if_stale: true }, silent: !manual }); }
      catch (error) { if (manual) Aira.reportError(error); return; }
      if (ctx.list) ctx.list.sync = result.sync;
      if (manual && result.reason === "not_connected") toast("LinkedIn is not connected. Connect it in Settings > Integrations to refresh your inbox.", "error", { timeout: 8000 });
      else if (manual && !result.started && result.reason !== "already_running") toast(result.message || "Monosage could not start a sync just now.", "", { timeout: 6000 });
      renderSyncBar();
      renderList();
      if (result.started || result.reason === "already_running") pollSync();
    }
    syncButton.addEventListener("click", () => startSync({ manual: true }));

    // Conversation list -----------------------------------------------------
    function renderChips() {
      const counts = (ctx.list && ctx.list.counts) || {};
      chips.replaceChildren(...FILTERS.map(([id, label]) => {
        const chip = h("button", { type: "button", class: "msgr-chip", "aria-pressed": ctx.filter === id ? "true" : "false" },
          label, counts[id] ? h("span", { class: "msgr-chip-count", text: String(counts[id]) }) : null);
        chip.addEventListener("click", () => {
          ctx.filter = id;
          Aira.remember("messages.filter", id);
          renderChips();
          loadList({ silent: true });
        });
        return chip;
      }));
    }

    function rowFor(conversation) {
      const selected = ctx.selected && ctx.selected.thread_id === conversation.thread_id;
      const prefix = conversation.last_sender === "us" ? "You: " : "";
      const badges = [];
      if (conversation.needs_reply) badges.push(h("span", { class: "badge stage needs_review", text: "Needs reply" }));
      if (conversation.auto) badges.push(h("span", { class: "badge easy", text: "Auto" }));
      if (conversation.is_recruiter) badges.push(h("span", { class: "badge stage saved", text: "Recruiter" }));
      if (conversation.last_status === "held") badges.push(h("span", { class: "badge stage failed", text: "Held" }));
      const button = h("button", {
        type: "button",
        class: `msgr-row${conversation.unread ? " unread" : ""}`,
        "data-roving-item": "1",
        "data-thread": conversation.thread_id,
        "aria-current": selected ? "true" : "false",
      },
        avatar(conversation.participant),
        h("span", { class: "msgr-row-main" },
          h("span", { class: "msgr-row-top" },
            h("span", { class: "msgr-name", text: conversation.participant, title: conversation.participant }),
            h("time", { class: "msgr-time", text: listTime(conversation) })),
          h("span", { class: "msgr-preview", text: conversation.snippet ? `${prefix}${conversation.snippet}` : (conversation.has_messages ? "" : "Open to load this conversation"), title: conversation.snippet || null }),
          badges.length ? h("span", { class: "msgr-badges" }, ...badges) : null),
        conversation.unread ? h("span", { class: "msgr-dot", title: "New activity", "aria-label": "New activity" }) : null);
      button.addEventListener("click", () => openThread(conversation));
      return h("div", { role: "listitem" }, button);
    }

    function skeletonRows(count) {
      return Array.from({ length: count }, () => h("div", { class: "msgr-skel-row", "aria-hidden": "true" },
        h("div", { class: "skeleton msgr-skel-avatar" }),
        h("div", { class: "msgr-skel-lines" }, h("div", { class: "skeleton", style: { width: "55%" } }), h("div", { class: "skeleton", style: { width: "90%" } }))));
    }

    function connectLinkedInAction() {
      return h("a", { class: "btn sm", href: "#/settings/integrations", text: "Open Settings > Integrations" });
    }

    // Rows are rebuilt on every poll and filter change. Keep keyboard focus on
    // the row that had it, and make only one row a tab stop (roving tabindex).
    function settleRows(focusedThread) {
      const items = [...rows.querySelectorAll(".msgr-row")];
      const current = items.find((item) => item.dataset.thread === focusedThread)
        || items.find((item) => item.getAttribute("aria-current") === "true")
        || items[0];
      for (const item of items) item.tabIndex = item === current ? 0 : -1;
      if (focusedThread && current && current.dataset.thread === focusedThread) current.focus({ preventScroll: true });
    }
    rows.addEventListener("focusin", (event) => {
      const row = event.target.closest && event.target.closest(".msgr-row");
      if (!row) return;
      for (const item of rows.querySelectorAll(".msgr-row")) item.tabIndex = item === row ? 0 : -1;
    });

    function emptyListState(list) {
      const sync = list.sync || {};
      if (list.saved_total) {
        if (!ctx.q && ctx.filter === "auto") return ui.empty("No conversation is set to Auto-reply", "Open a conversation and switch it to Auto-reply, or choose which conversations automatic replies cover in the Automatic replies panel above.");
        if (!ctx.q && ctx.filter === "needs_reply") return ui.empty("Nothing needs a reply", "Every conversation Monosage has saved is answered or does not ask for a reply.");
        if (!ctx.q && ctx.filter === "recruiters") return ui.empty("No recruiter conversations yet", "Conversations that read like recruiter outreach appear here once Monosage has seen them.");
        return ui.empty("No matching conversations", list.message || "Try a different search or filter.");
      }
      if (!list.linkedin_connected) return ui.empty("Connect LinkedIn to see your messages", "Monosage keeps its own copy of your conversations, so you can read and answer recruiters here without opening LinkedIn. Connect your account once and your inbox loads automatically.", connectLinkedInAction());
      if (sync.running || sync.status === "idle") return h("div", {}, ...skeletonRows(6), h("p", { class: "msgr-empty-note", text: "Loading your inbox from LinkedIn…" }));
      if (sync.status === "error" || sync.status === "interrupted") return ui.empty("Could not load your inbox", sync.error || "LinkedIn did not answer. Try again in a moment.", h("button", { type: "button", class: "btn sm", text: "Try again", onclick: () => startSync({ manual: true }) }));
      return ui.empty("Your inbox is empty", "LinkedIn returned no conversations.");
    }

    function renderList() {
      const list = ctx.list;
      renderChips();
      renderSyncBar();
      banner.replaceChildren();
      const active = document.activeElement;
      const focusedThread = active && active.classList && active.classList.contains("msgr-row") && rows.contains(active) ? active.dataset.thread : null;
      if (!list) { rows.replaceChildren(...skeletonRows(6)); return; }
      if (!list.linkedin_connected && list.saved_total) {
        banner.append(h("div", { class: "msgr-banner" },
          h("span", { text: "LinkedIn is not connected. You are reading the copy saved in Monosage." }),
          h("a", { href: "#/settings/integrations", text: "Connect" })));
      }
      if (!list.conversations.length) { rows.replaceChildren(emptyListState(list)); return; }
      rows.replaceChildren(...list.conversations.map(rowFor));
      settleRows(focusedThread);
    }

    async function loadList({ silent = false } = {}) {
      const token = ++ctx.listToken;
      if (!silent && !ctx.list) renderList();
      const params = new URLSearchParams({ filter: ctx.filter, limit: "200" });
      if (ctx.q) params.set("q", ctx.q);
      let data;
      try { data = await api(`/api/inbox?${params.toString()}`, { silent: true }); }
      catch (error) {
        if (token !== ctx.listToken) return null;
        if (!ctx.list) rows.replaceChildren(ui.error(error, () => loadList()));
        return null;
      }
      if (token !== ctx.listToken) return null;
      ctx.list = data;
      renderList();
      if (data.syncing) pollSync();
      return data;
    }

    let searchTimer;
    search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { ctx.q = search.value.trim(); loadList({ silent: true }); }, 250);
    });
    search.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") { const first = rows.querySelector(".msgr-row[tabindex='0']") || rows.querySelector("[data-roving-item]"); if (first) { event.preventDefault(); first.focus(); } }
      if (event.key === "Escape" && search.value) { search.value = ""; ctx.q = ""; loadList({ silent: true }); }
    });

    // Thread ----------------------------------------------------------------
    function backBar() {
      return h("button", { type: "button", class: "btn ghost sm msgr-back", "aria-label": "Back to conversations", onclick: closeThread }, h("span", { "aria-hidden": "true", text: "←" }), h("span", { text: "Conversations" }));
    }
    function closeThread({ navigate = true } = {}) {
      const closing = ctx.selected ? ctx.selected.thread_id : "";
      setView("list");
      ctx.selected = null;
      ctx.thread = null;
      ctx.token += 1;
      if (navigate) {
        // Undo the history entry opening the thread added, so Back stays in step.
        if (ctx.pushed) { try { history.back(); } catch { setThreadParam(""); } }
        else setThreadParam("");
      }
      ctx.pushed = false;
      for (const row of rows.querySelectorAll(".msgr-row")) row.setAttribute("aria-current", "false");
      threadPane.replaceChildren(emptyThread());
      // Hand keyboard focus back to the row the thread was opened from.
      const row = closing && [...rows.querySelectorAll(".msgr-row")].find((item) => item.dataset.thread === closing);
      if (row) row.focus({ preventScroll: true });
    }
    function emptyThread() {
      return h("div", { class: "msgr-placeholder" },
        h("div", { class: "msgr-placeholder-mark", "aria-hidden": "true", text: "✉" }),
        h("strong", { text: "Select a conversation" }),
        h("p", { text: "Read it here, draft a reply with your local model, and send it from Monosage. Switch a conversation to Auto-reply to let Monosage answer during inbox checks." }));
    }
    function threadSkeleton(name) {
      return h("div", { class: "msgr-thread" },
        h("div", { class: "msgr-thread-head" }, backBar(), name ? avatar(name, true) : h("div", { class: "skeleton msgr-skel-avatar lg" }), h("div", { class: "msgr-thread-title" }, name ? h("h3", { text: name }) : h("div", { class: "skeleton", style: { width: "180px", height: "18px" } }))),
        h("div", { class: "msgr-scroll" }, ui.skeleton(4)));
    }

    function highlightRows() {
      for (const row of rows.querySelectorAll(".msgr-row")) row.setAttribute("aria-current", ctx.selected && row.dataset.thread === ctx.selected.thread_id ? "true" : "false");
    }

    async function openThread(conversation, { navigate = true, focus = true } = {}) {
      if (!conversation || !conversation.thread_id) return;
      const token = ++ctx.token;
      const known = (ctx.list && ctx.list.conversations.find((item) => item.thread_id === conversation.thread_id)) || {};
      const alreadyOpen = !!ctx.selected;
      ctx.selected = { thread_id: conversation.thread_id, participant: conversation.participant || known.participant || "" };
      ctx.thread = null;
      setView("thread");
      if (navigate) setThreadParam(conversation.thread_id, { push: !alreadyOpen });
      // On a phone the thread takes the whole screen: start from its top.
      if (focus && window.matchMedia("(max-width: 900px)").matches) window.scrollTo({ top: 0 });
      highlightRows();
      threadPane.replaceChildren(threadSkeleton(ctx.selected.participant));
      let data;
      try { data = await api(`/api/conversations/${enc(conversation.thread_id)}`, { silent: true }); }
      catch (error) {
        if (token !== ctx.token) return;
        threadPane.replaceChildren(h("div", { class: "msgr-thread" }, h("div", { class: "msgr-thread-head" }, backBar()), h("div", { class: "msgr-scroll" }, ui.error(error, () => openThread(conversation)))));
        return;
      }
      if (token !== ctx.token) return;
      const view = buildThread(ctx.selected, data, token);
      ctx.thread = view;
      threadPane.replaceChildren(view.root);
      view.scrollToEnd();
      // Move keyboard focus into the thread (the list row that was activated is
      // rebuilt by the list reload below): Escape, Tab and the composer work from here.
      if (focus) view.focusThread();
      loadList({ silent: true });
      // The saved copy is out of date: check this one conversation without
      // blocking the view. The server skips LinkedIn if a sync confirmed it meanwhile.
      if (data.linkedin_connected && data.source !== "live" && data.stale) view.refresh({ auto: true });
    }

    async function reloadOpenThread() {
      if (!ctx.selected || !ctx.thread) return;
      const { thread_id: threadId } = ctx.selected;
      const token = ctx.token;
      try {
        const data = await api(`/api/conversations/${enc(threadId)}`, { silent: true });
        if (token === ctx.token && ctx.thread) ctx.thread.update(data);
      } catch { /* the open thread stays as it is */ }
    }

    function messageBubbles(data) {
      const nodes = [];
      let day = "";
      let previousSender = "";
      for (const message of data.messages) {
        const key = message.sent_on || message.date || "";
        if (key !== day) {
          day = key;
          previousSender = "";
          const parsed = parseLocal(message.sent_on);
          const label = parsed ? shortDay(parsed.date) : (message.date || "");
          if (label) nodes.push(h("div", { class: "msgr-day" }, h("span", { text: label })));
        }
        // Your own messages group together whatever name LinkedIn or Aira put on them.
        const who = message.own ? "own" : `them|${message.sender}`;
        const first = previousSender !== who;
        previousSender = who;
        nodes.push(h("div", { class: `msgr-msg${message.own ? " own" : ""}${first ? " first" : ""}` },
          h("div", { class: "msgr-bubble", title: message.source === "aira" ? "Sent from Monosage. LinkedIn has not listed this message yet." : null },
            first && !message.own ? h("div", { class: "msgr-sender", text: message.sender }) : null,
            h("div", { class: "msgr-text", text: message.text }),
            h("div", { class: "msgr-stamp" }, message.time || "", message.source === "aira" ? h("span", { class: "msgr-via", text: " · sent from Monosage" }) : null))));
      }
      return nodes;
    }

    function buildThread(conversation, firstData, token) {
      const threadId = conversation.thread_id;
      let data = firstData;
      let participant = data.participant || conversation.participant || "LinkedIn contact";
      const model = () => (Aira.state.settings && Aira.state.settings.default_model) || "qwen3.5:4b";

      const head = h("div", { class: "msgr-thread-head" });
      const noteBox = h("div", {});
      const scroll = h("div", { class: "msgr-scroll", role: "log", "aria-live": "polite", tabindex: "0" });
      const historyBox = h("div", {});

      // Composer: created once so a refresh never discards what is being typed.
      const draft = h("textarea", { rows: 2, maxlength: 3000, placeholder: "Write a reply, or draft one with AI…", "aria-label": `Reply to ${participant}`, id: `reply-${threadId.slice(0, 12)}` },
        ctx.drafts[threadId] !== undefined ? ctx.drafts[threadId] : (data.draft ? data.draft.reply : ""));
      const counter = h("small", { class: "muted" });
      const fit = () => { draft.style.height = "auto"; draft.style.height = `${Math.min(draft.scrollHeight + 2, 190)}px`; };
      const updateCounter = () => { ctx.drafts[threadId] = draft.value; counter.textContent = `${draft.value.trim().length.toLocaleString()} / 3,000`; fit(); };
      draft.addEventListener("input", updateCounter);
      draft.addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); sendButton.click(); } });

      const draftButton = h("button", { type: "button", class: "btn secondary sm", text: "Draft with AI" });
      draftButton.addEventListener("click", () => withBusy(draftButton, async () => {
        draftButton.textContent = "Drafting…";
        const result = await api(`/api/conversations/${enc(threadId)}/draft`, { method: "POST", body: { model: model() } });
        draft.value = result.draft ? result.draft.reply : "";
        updateCounter();
        draft.focus();
        toast(result.needs_reply === false ? "The latest message may not need a reply. Review before sending." : "Draft ready. Review it, then send.", "success");
      }));
      const saveButton = h("button", { type: "button", class: "btn ghost sm", text: "Save draft" });
      saveButton.addEventListener("click", () => withBusy(saveButton, async () => {
        await api(`/api/conversations/${enc(threadId)}/draft`, { method: "PATCH", body: { participant, reply: draft.value } });
        toast("Draft saved.", "success");
      }));
      const sendButton = h("button", { type: "button", class: "btn sm msgr-send", text: "Send now" });
      sendButton.addEventListener("click", async () => {
        const text = draft.value.trim();
        if (!text) { toast("Write or draft a reply first.", "error"); draft.focus(); return; }
        const seen = data.messages.length;
        // Say so when the copy being answered may be behind LinkedIn.
        const checked = data.confirmed_at || data.synced_at;
        const outdated = data.stale || data.live_error
          ? ` Monosage last checked this conversation ${checked ? fmt.ago(checked) : "a while ago"}${data.live_error ? " and could not check LinkedIn just now" : ""}, so newer messages may exist.`
          : "";
        const confirmed = await Aira.confirm({
          title: `Send this reply to ${participant}?`,
          message: (text.length > 400 ? `${text.slice(0, 400)}…` : text) + outdated,
          confirmLabel: "Send reply",
        });
        if (!confirmed) return;
        await withBusy(sendButton, async () => {
          sendButton.textContent = "Sending…";
          try {
            await api(`/api/conversations/${enc(threadId)}/send`, { method: "POST", body: { reply: text, participant, confirm: true, seen_messages: seen } });
          } catch (error) {
            // New messages were saved while the reply was written: show them first.
            if (/New messages arrived/.test(error.message || "")) await reloadOpenThread();
            throw error;
          }
          toast(`Reply sent to ${participant}.`, "success");
          draft.value = "";
          delete ctx.drafts[threadId];
          updateCounter();
          if (token === ctx.token) await reloadOpenThread();
          loadList({ silent: true });
          renderStatusPanel(autoBox, onReview);
        });
      });
      const composerHint = h("span", { class: "msgr-composer-hint" });
      const composer = h("div", { class: "msgr-composer" },
        draft,
        h("div", { class: "msgr-composer-bar" }, counter, composerHint, h("div", { class: "btn-row" }, draftButton, saveButton, sendButton)));
      updateCounter();

      let refreshing = false;
      // Sending waits for the check of a stale copy, so no one answers old news.
      function syncComposer() {
        const connected = !!data.linkedin_connected;
        draftButton.disabled = !connected;
        sendButton.disabled = !connected || refreshing;
        composerHint.textContent = refreshing
          ? "Checking LinkedIn for new messages…"
          : connected ? (data.effective_mode === "auto" ? "Auto-reply is on for this conversation." : "Ctrl+Enter to send") : "Connect LinkedIn to send or draft replies.";
      }
      const refreshButton = h("button", { type: "button", class: "btn ghost sm icon", "aria-label": "Refresh this conversation from LinkedIn", title: "Refresh this conversation from LinkedIn" }, h("span", { "aria-hidden": "true", text: "↻" }));
      async function refresh({ auto = false } = {}) {
        if (refreshing) return;
        refreshing = true;
        refreshButton.disabled = true;
        refreshButton.classList.add("spinning");
        syncComposer();
        noteBox.replaceChildren(h("div", { class: "msgr-banner info" }, h("span", { text: "Checking LinkedIn for new messages…" })));
        try {
          const fresh = await api(`/api/conversations/${enc(threadId)}?refresh=1${auto ? "&auto=1" : ""}`, { silent: true });
          if (token === ctx.token) update(fresh);
          loadList({ silent: true });
        } catch (error) {
          if (token === ctx.token) noteBox.replaceChildren(h("div", { class: "msgr-banner warn" }, h("span", { text: `Could not refresh: ${error.message}` })));
        } finally {
          refreshing = false;
          refreshButton.disabled = !data.linkedin_connected;
          refreshButton.classList.remove("spinning");
          syncComposer();
        }
      }
      refreshButton.addEventListener("click", () => refresh());

      function renderHead() {
        const policy = data.policy || { mode: "manual" };
        const shownMode = policy.mode_set_by_user ? policy.mode : (data.effective_mode || "manual");
        // A conversation Aira has never saved has no policy or recruiter to change yet.
        const saved = data.saved !== false;
        const modeSwitch = h("div", { class: "subnav msgr-mode", role: "group", "aria-label": "Reply mode", style: { margin: "0" } });
        for (const [mode, label] of [["manual", "Manual send"], ["auto", "Auto-reply"]]) {
          const button = h("button", { type: "button", "aria-pressed": shownMode === mode ? "true" : "false", text: label });
          button.addEventListener("click", async () => {
            if (policy.mode_set_by_user && policy.mode === mode) return;
            if (mode === "auto") {
              const confirmed = await Aira.confirm({
                title: `Auto-reply to ${participant}?`,
                message: "During inbox checks Monosage will draft and send replies in this conversation for you. It holds any reply about pay, visa or work authorization, personal details, offers, relocation, links, or contact details for your review, waits at least 6 hours between automatic replies here, and stops at your daily limit. You can switch back to Manual at any time.",
                confirmLabel: "Turn on Auto-reply",
              });
              if (!confirmed) return;
            }
            await withBusy(button, async () => {
              const result = await api(`/api/conversations/${enc(threadId)}/policy`, { method: "PATCH", body: { mode, participant } });
              if (mode === "auto" && !result.schedule_active) toast("Auto-reply is on. Turn on inbox checks in the Automatic replies panel so Monosage can reply.", "success", { timeout: 8000 });
              else toast(mode === "auto" ? `Auto-reply is on for ${participant}.` : `${participant} is back to manual sending.`, "success");
              renderStatusPanel(autoBox, onReview);
              await reloadOpenThread();
              loadList({ silent: true });
            });
          });
          modeSwitch.append(button);
        }
        const useDefault = policy.mode_set_by_user
          ? h("button", { type: "button", class: "btn ghost sm", text: "Use default", title: "Follow the default setting for all conversations", onclick: (event) => withBusy(event.currentTarget, async () => {
              await api(`/api/conversations/${enc(threadId)}/policy`, { method: "PATCH", body: { mode: "default", participant } });
              toast("This conversation now follows the default setting.", "success");
              await reloadOpenThread();
              loadList({ silent: true });
            }) })
          : null;
        const recruiterAction = policy.recruiter_id
          ? h("a", { class: "btn ghost sm", href: `#/recruiters/${policy.recruiter_id}`, text: `Recruiter: ${policy.recruiter_name || participant}` })
          : h("button", { type: "button", class: "btn ghost sm", text: "Save as recruiter" });
        if (!policy.recruiter_id) {
          recruiterAction.addEventListener("click", () => withBusy(recruiterAction, async () => {
            await api(`/api/conversations/${enc(threadId)}/recruiter`, { method: "POST", body: { participant } });
            toast(`${participant} saved to Recruiters. Sent replies are logged there.`, "success");
            await reloadOpenThread();
            loadList({ silent: true });
          }));
        }
        refreshButton.disabled = refreshing || !data.linkedin_connected;
        refreshButton.title = data.linkedin_connected ? "Refresh this conversation from LinkedIn" : "Connect LinkedIn to refresh";
        const sub = [];
        sub.push(data.synced_at ? `Updated ${fmt.ago(data.synced_at)}` : saved ? "Not refreshed yet" : "Not saved in Monosage yet");
        if (data.message_count) sub.push(fmt.plural(data.message_count, "message"));
        head.replaceChildren(
          backBar(),
          avatar(participant, true),
          h("div", { class: "msgr-thread-title" },
            h("h3", { text: participant }),
            h("div", { class: "msgr-thread-sub" }, ...[saved ? modeBadge(shownMode) : null, h("span", { text: sub.join(" · ") })])),
          h("div", { class: "msgr-thread-actions" }, saved ? modeSwitch : null, saved ? useDefault : null, saved ? recruiterAction : null, refreshButton));
      }

      function renderBody() {
        const nearEnd = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
        const previousCount = scroll.dataset.count ? Number(scroll.dataset.count) : -1;
        const nodes = [];
        if (data.messages.length) nodes.push(...messageBubbles(data));
        else if (data.conversation) nodes.push(h("div", { class: "description", text: data.conversation }));
        else nodes.push(h("div", { class: "msgr-thread-empty" },
          h("strong", { text: "No messages to show" }),
          h("p", { text: data.note || "Nothing is saved for this conversation yet." }),
          ...(data.linkedin_connected ? [] : [connectLinkedInAction()])));
        const whoLast = data.speaker === "them" ? `${participant} wrote last. A reply is due.` : data.speaker === "us" ? "You wrote last. Nothing to answer yet." : "";
        // Element.replaceChildren would print a null as the text "null".
        scroll.replaceChildren(...nodes, ...(whoLast ? [h("p", { class: "msgr-wholast", text: whoLast })] : []), historyBox);
        scroll.dataset.count = String(data.messages.length);
        if (nearEnd || previousCount !== data.messages.length) scrollToEnd();
      }

      function renderNote() {
        noteBox.replaceChildren();
        if (data.live_error) noteBox.append(h("div", { class: "msgr-banner warn" }, h("span", { text: `Showing the saved copy. LinkedIn did not answer: ${data.live_error}` }), h("button", { type: "button", class: "btn ghost sm", text: "Try again", onclick: () => refresh() })));
        syncComposer();
      }

      function renderHistory() {
        const sends = data.sends || [];
        historyBox.replaceChildren(h("details", { class: "msgr-history" },
          h("summary", { text: sends.length ? `Monosage activity in this conversation (${sends.length})` : "Monosage activity in this conversation" }),
          sends.length
            ? h("ul", { class: "timeline" }, ...sends.map((item) => h("li", {},
                h("div", { class: "btn-row" }, statusBadge(item.status) || h("span", { class: "badge", text: item.status }), h("span", { class: "badge", text: item.mode === "auto" ? "Automatic" : "Sent by you" })),
                h("div", { text: item.reply.length > 220 ? `${item.reply.slice(0, 220)}…` : item.reply }),
                item.detail ? h("small", { text: item.detail }) : null,
                h("small", { text: fmt.datetime(item.created_at) }))))
            : h("p", { class: "muted", text: "Nothing has been sent in this conversation from Monosage yet." })));
      }

      function scrollToEnd() {
        requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; });
      }

      function update(next) {
        data = next;
        participant = data.participant || participant;
        ctx.selected.participant = participant;
        renderHead();
        renderNote();
        renderBody();
        renderHistory();
        if (data.mode_reason) composer.title = data.mode_reason;
      }

      const root = h("div", { class: "msgr-thread" }, head, noteBox, scroll, composer);
      update(firstData);
      const focusThread = () => scroll.focus({ preventScroll: true });
      return { root, update, refresh, scrollToEnd, focusThread };
    }

    // Keyboard: Escape returns to the list on small screens.
    messenger.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && messenger.getAttribute("data-view") === "thread" && window.matchMedia("(max-width: 900px)").matches && !event.defaultPrevented) {
        const tag = (event.target.tagName || "").toLowerCase();
        if (tag === "textarea" || tag === "input") return;
        closeThread();
      }
    });

    // Boot ------------------------------------------------------------------
    threadPane.replaceChildren(emptyThread());
    renderList();
    const data = await loadList();
    if (data && data.linkedin_connected && !data.syncing) startSync({ manual: false });
    if (route && route.query.thread) openThread({ thread_id: route.query.thread, participant: route.query.participant || "" }, { navigate: false, focus: false });

    // Keep the saved copy warm while the tab stays open. The server decides
    // whether a sync is due, so this never starts more than one.
    messengerTimer = setInterval(async () => {
      if (!document.body.contains(messenger)) { clearInterval(messengerTimer); messengerTimer = null; stopPolling(); return; }
      if (document.hidden) return;
      const latest = await loadList({ silent: true });
      if (latest && latest.linkedin_connected && !latest.syncing) startSync({ manual: false });
    }, 60000);
  };
})();

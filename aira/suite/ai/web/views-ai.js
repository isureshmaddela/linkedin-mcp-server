/* Aira — AI (#/ai): chat with the master agent and manage the agents it works through.
 *
 * Tabs (?tab=): chat (conversations kept per person, found by search, renamed and saved as text; suggestions, a
 * morning brief, voice input where the browser has it, actions with Approve, Reject and Undo), agents (one per app:
 * on or off, consent level, the most actions it may take alone a day, operations and success rate, consent levels
 * explained, recent activity), approvals (actions that wait for a yes), routines (a question or command Aira runs
 * at a set time: the answer pops up and shows on Today), memory (what the person asked Aira to remember; every
 * answer takes it into account), earn (what the apps earned, earning goals and their pace, next steps and ways to
 * earn) and activity (every read and action, with search, filters, Undo and CSV). Answers can be marked helpful
 * or not. Everything runs through /api/ai/* (aira/suite/ai). DOM is built
 * with h() and text only: answers are shown as text, never parsed as HTML.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("ai", path, opts);
  const put = (node, ...children) => node.append(...children.filter(Boolean));
  const tick = () => { if (Aira.life && Aira.life.tick) Aira.life.tick(); };

  const CONSENT = { read_only: "Read only", auto_minor: "Small actions alone", auto_major: "All actions alone", manual_approval: "Ask me first" };
  const STATUS_TONES = { done: "good", failed: "bad", blocked: "bad", pending: "warn", rejected: "", undone: "" };
  const STATUS_LABELS = { done: "Done", failed: "Failed", blocked: "Not allowed", pending: "Waiting for you", rejected: "Rejected", undone: "Undone" };
  const DAYS = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
  const appName = (id) => { if (id === "ai") return "All your apps"; const app = Aira.APPS.find((entry) => entry.id === id); return app ? app.name : id === "hub" ? "Business hub" : id; };
  const statusPill = (status) => h("span", { class: `suite-pill as-written ${STATUS_TONES[status] || ""}`, text: STATUS_LABELS[status] || status });
  const seconds = (ms) => (ms === null || ms === undefined ? "-" : `${(ms / 1000).toFixed(1)}s`);
  const tokens = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n || 0));
  const plural = (n, one, many) => `${Number(n || 0).toLocaleString()} ${n === 1 ? one : many}`;
  const SOURCE = { model: "Monosage's model", template: "Built-in answers", command: "Done at once" };

  async function undoOp(op) {
    if (!(await Aira.confirm({ title: "Undo this", message: `Undo "${op.summary}"? Monosage takes it back in its app.`, confirmLabel: "Undo" }))) return null;
    const result = await api(`/ops/${op.id}/undo`, { method: "POST", body: {} });
    Aira.toast(result.op.result || "Undone.", "success");
    tick();
    return result.op;
  }

  // ---------------------------------------------------------------- chat
  const state = { session: "", messages: [], stats: null, busy: false, search: "" };

  function actionChip(action, rerender) {
    const decide = (approve) => S.button(approve ? "Approve" : "Reject", async () => {
      const result = await api(`/ops/${action.id}/${approve ? "approve" : "reject"}`, { method: "POST", body: {} });
      Object.assign(action, { status: result.op.status, result: result.op.result, can_decide: false, can_undo: result.op.can_undo });
      Aira.toast(result.op.status === "done" ? result.op.result : result.op.status === "rejected" ? "Rejected." : result.op.result, result.op.status === "done" ? "success" : "");
      rerender();
      tick();
    }, { kind: approve ? "sm" : "ghost sm" });
    const undo = action.can_undo ? S.button("Undo", async () => {
      const op = await undoOp(action);
      if (!op) return;
      Object.assign(action, { status: op.status, result: op.result, can_undo: false });
      rerender();
    }, { kind: "ghost sm" }) : null;
    return h("div", { class: "ai-action" },
      h("div", { class: "ai-action-main" }, h("strong", { text: action.summary }), h("span", { class: "muted small", text: `${action.agent_name} · ${action.result || ""}` })),
      statusPill(action.status),
      action.can_decide || undo ? h("div", { class: "btn-row" }, action.can_decide ? decide(true) : null, action.can_decide ? decide(false) : null, undo) : null);
  }

  function bubble(message, rerender) {
    const mine = message.role === "user";
    const actions = message.actions || [];
    const saved = !mine && /^[0-9a-f]{32}$/.test(message.id || "");
    const meta = mine ? null : h("div", { class: "ai-meta" },
      h("span", { text: message.agent || "Master agent" }),
      message.tokens ? h("span", { text: `about ${message.tokens} tokens` }) : null,
      message.duration_ms ? h("span", { text: seconds(message.duration_ms) }) : null,
      message.source ? h("span", { text: SOURCE[message.source] || message.source }) : null,
      saved ? rateButtons(message) : null);
    // With actions, the answer is shown without the lines that say what they did: the action cards say it, up to
    // date (a plain command's answer is only those lines).
    const said = mine || !actions.length ? message.content : (message.reply === undefined ? message.content : message.reply);
    const text = said ? h("p", { class: "ai-text", text: said }) : null;
    return h("div", { class: `ai-msg ${mine ? "mine" : "theirs"}` },
      h("span", { class: "ai-avatar", "aria-hidden": "true" }, mine ? h("span", { text: (Aira.state.user && Aira.state.user.email ? Aira.state.user.email[0] : "Y").toUpperCase() }) : Aira.icon("ai")),
      h("div", { class: "ai-bubble" },
        h("span", { class: "sr-only", text: mine ? "You said:" : "Monosage said:" }),
        text,
        ...actions.map((action) => actionChip(action, rerender)),
        meta));
  }

  function rateButtons(message) {
    const row = h("span", { class: "ai-rate", role: "group", "aria-label": "Was this answer helpful?" });
    const draw = () => {
      const button = (value, label) => h("button", { type: "button", class: `btn ghost sm ai-rate-btn ${message.rating === value ? "on" : ""}`, "aria-pressed": message.rating === value ? "true" : "false", text: label, onclick: async () => {
        const next = message.rating === value ? 0 : value;
        try {
          const result = await api(`/messages/${message.id}/rate`, { method: "POST", body: { rating: next } });
          message.rating = result.message.rating;
          draw();
          if (next) Aira.toast(next > 0 ? "Thanks. Glad it helped." : "Thanks. Monosage will keep it in mind.", "success");
        } catch (error) { Aira.reportError(error); }
      } });
      row.replaceChildren(button(1, "Helpful"), button(-1, "Not helpful"));
    };
    draw();
    return row;
  }

  // The conversation's numbers, one slim line under its name (nothing until Aira has answered).
  function statsRow(stats) {
    const s = stats || {};
    if (!s.answers) return [];
    const item = (label, value) => h("span", { class: "ai-stat" }, h("span", { class: "muted", text: `${label}: ` }), h("strong", { text: value }));
    return [h("div", { class: "ai-stats", role: "group", "aria-label": "This conversation" },
      item("Agent", s.agent || "Master agent"),
      s.tokens ? item("Tokens", `${tokens(s.tokens)} (estimate)`) : null,
      item("Avg response", `${seconds(s.avg_ms)} over ${plural(s.answers, "answer", "answers")}`),
      item("Answers from", SOURCE[s.source] || "-"),
      s.source === "template" ? h("a", { class: "small", href: "#/settings", text: "Start the writing model in Settings for fuller answers" }) : null)];
  }

  async function chatTab(body, route) {
    state.session = route.query.session || "";
    const [sessionsData, agentsData] = await Promise.all([api(`/sessions${state.search ? `?q=${encodeURIComponent(state.search)}` : ""}`), api("/agents")]);
    const on = agentsData.agents.filter((a) => a.enabled);
    let title = "Conversation";
    if (state.session) {
      try {
        const data = await api(`/sessions/${state.session}`, { silent: true });
        state.messages = data.messages;
        state.stats = data.stats;
        title = data.session.title;
      } catch (error) {
        if (error.status !== 404) throw error;
        Aira.toast("That conversation was deleted. Here is a new one.");
        state.session = "";
      }
    }
    if (!state.session) { state.messages = []; state.stats = null; }

    const statsHolder = h("div", { class: "ai-stats-holder" }, ...statsRow(state.stats));
    const log = h("div", { class: "ai-log", role: "log", "aria-live": "polite", "aria-label": "Conversation" });
    const input = h("textarea", { class: "ai-input", rows: 1, maxlength: 2000, placeholder: "Ask anything about your life…", "aria-label": "Message to Monosage" });
    const send = h("button", { type: "button", class: "btn ai-send", "aria-label": "Send", title: "Send" }, h("span", { text: "Send" }));
    const has = (id) => on.some((a) => a.id === id);

    const suggestions = [
      ["Morning brief", "Give me my morning brief"],
      has("life") ? ["How are my goals going?", "How are my goals going?"] : null,
      has("planner") ? ["What's on my plate today?", "What do I need to do today?"] : null,
      has("health") ? ["My medicines today", "Which medicines do I take today?"] : null,
      has("earn") ? ["How can I earn more?", "How much did I earn this month and how can I earn more?"] : null,
      has("stocks") ? ["My stocks today", "How are my stocks doing today?"] : null,
      has("memory") ? ["What do you remember?", "What do you remember about me?"] : null,
      has("business") ? ["Business insights", "How's my business doing?"] : null,
      has("planner") ? ["Add milk to shopping", "Add milk and bread to the shopping list"] : null,
      has("life") ? ["Set a goal", "Add a goal: read 12 books in 12 months"] : null,
    ].filter(Boolean);

    const draw = () => {
      if (!state.messages.length) {
        log.replaceChildren(h("div", { class: "ai-welcome" },
          h("span", { class: "ai-welcome-mark", "aria-hidden": "true" }, Aira.icon("ai")),
          h("h3", { text: "Welcome to Monosage AI" }),
          h("p", { class: "muted", text: on.length ? `I work through ${on.length} agents, one for each of your apps: ${on.map((a) => a.name).join(", ")}.` : "Every agent is switched off. Switch some on in the Agents tab." }),
          h("p", { class: "muted", text: "Ask about your day, goals, health, home, money or business, or ask me to add something. You can undo what I do. Say \"Remember that ...\" and I will keep it in mind." }),
          h("div", { class: "ai-suggest" }, ...suggestions.map(([label, text]) => h("button", { type: "button", class: "btn secondary", text: label, onclick: () => { input.value = text; ask(); } })))));
        return;
      }
      log.replaceChildren(...state.messages.map((message) => bubble(message, draw)));
      if (state.busy) log.append(h("div", { class: "ai-msg theirs" }, h("span", { class: "ai-avatar", "aria-hidden": "true" }, Aira.icon("ai")), h("div", { class: "ai-bubble ai-typing", role: "status", "aria-label": "Monosage is thinking" }, h("span", {}), h("span", {}), h("span", {}))));
      log.scrollTop = log.scrollHeight;
    };

    async function ask() {
      const text = input.value.trim();
      if (!text || state.busy) { input.focus(); return; }
      state.busy = true;
      send.disabled = true;
      input.value = "";
      input.style.height = "auto";
      state.messages = [...state.messages, { id: `local${Date.now()}`, role: "user", content: text, actions: [] }];
      draw();
      try {
        const result = await api("/chat", { method: "POST", body: state.session ? { message: text, session_id: state.session } : { message: text }, silent: true });
        if (!state.session) {
          state.session = result.session_id;
          history.replaceState(null, "", `#/ai?tab=chat&session=${result.session_id}`);
          refreshSessions();
          headTitle.textContent = text.slice(0, 60);
          headActions.hidden = false;
        }
        state.messages = [...state.messages.slice(0, -1), result.question, result.answer];
        state.stats = result.stats;
        statsHolder.replaceChildren(...statsRow(state.stats));
        if ((result.answer.actions || []).some((a) => a.status === "done" || a.status === "pending")) tick();
        if (Aira.life && Aira.life.prefs && Aira.life.prefs.read_aloud && Aira.life.speak) Aira.life.speak(result.answer.content);
      } catch (error) {
        state.messages = [...state.messages, { id: `err${Date.now()}`, role: "assistant", content: `I could not answer: ${error.message}`, agent: "Master agent", actions: [] }];
      } finally {
        state.busy = false;
        send.disabled = false;
        draw();
        input.focus();
      }
    }
    hooks.new = () => input.focus();
    send.addEventListener("click", ask);
    input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask(); } });
    input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = `${Math.min(160, input.scrollHeight)}px`; });

    // Voice input where the browser can do it (Chrome, Edge, Safari); nothing is sent anywhere until you press Send.
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    let mic = null;
    if (Recognition) {
      mic = h("button", { type: "button", class: "btn ghost icon", "aria-label": "Speak your message", title: "Speak your message", "aria-pressed": "false" }, Aira.icon("mic"));
      let listening = null;
      mic.addEventListener("click", () => {
        if (listening) { listening.stop(); return; }
        const recognition = new Recognition();
        recognition.lang = document.documentElement.lang === "te" ? "te-IN" : document.documentElement.lang === "hi" ? "hi-IN" : "en-US";
        recognition.interimResults = true;
        recognition.onresult = (event) => { input.value = Array.from(event.results).map((r) => r[0].transcript).join(" "); };
        recognition.onend = () => { listening = null; mic.setAttribute("aria-pressed", "false"); mic.classList.remove("on"); input.focus(); };
        recognition.onerror = () => { Aira.toast("The microphone did not work. Type your message instead."); };
        listening = recognition;
        mic.setAttribute("aria-pressed", "true");
        mic.classList.add("on");
        recognition.start();
      });
    }

    // Conversations: a list with search; on a phone it folds away above the chat.
    const sessionList = h("ul", { class: "ai-sessions" });
    const countLabel = h("span", { class: "muted small" });
    function drawSessions(sessions) {
      countLabel.textContent = sessions.length ? `(${sessions.length})` : "";
      sessionList.replaceChildren(...(sessions.length ? sessions.map((session) => h("li", { class: session.id === state.session ? "current" : "" },
        h("a", { href: `#/ai?tab=chat&session=${session.id}`, "aria-current": session.id === state.session ? "page" : "false" }, h("span", { class: "ai-session-title", text: session.title }), h("span", { class: "muted small", text: Aira.fmt.ago(session.updated_at) })),
        h("button", { type: "button", class: "btn ghost icon sm", "aria-label": `Delete conversation: ${session.title}`, title: "Delete", text: "×", onclick: async () => {
          if (!(await S.confirmDelete("this conversation"))) return;
          await api(`/sessions/${session.id}`, { method: "DELETE" });
          if (session.id === state.session) Aira.router.go(["ai"], { tab: "chat" }); else refreshSessions();
        } }))) : [h("li", { class: "muted small ai-sessions-empty", text: state.search ? `No conversation mentions "${state.search}".` : "Your conversations appear here." })]));
    }
    async function refreshSessions() { drawSessions((await api(`/sessions${state.search ? `?q=${encodeURIComponent(state.search)}` : ""}`, { silent: true })).sessions); }
    drawSessions(sessionsData.sessions);
    const search = h("input", { type: "search", class: "ai-session-search", placeholder: "Search", "aria-label": "Search conversations", value: state.search });
    let searchTimer = 0;
    search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.search = search.value.trim(); refreshSessions(); }, 250); });

    const side = h("details", { class: "card ai-side", "aria-label": "Conversations" });
    side.open = window.matchMedia ? !window.matchMedia("(max-width: 760px)").matches : true;
    put(side,
      h("summary", { class: "ai-side-head" }, h("strong", { text: "Conversations " }), countLabel),
      h("div", { class: "ai-side-tools" }, search, S.button("New", () => Aira.router.go(["ai"], { tab: "chat" }), { kind: "ghost sm" })),
      sessionList);

    const headTitle = h("h2", { text: title });
    const rename = S.button("Rename", async () => {
      await S.edit({ title: "Rename conversation", fields: [{ name: "title", label: "Name", required: true }], values: { title: headTitle.textContent }, onSave: async (values) => {
        const result = await api(`/sessions/${state.session}`, { method: "PATCH", body: values });
        headTitle.textContent = result.session.title;
        refreshSessions();
      } });
    }, { kind: "ghost sm" });
    const save = S.button("Save as text", () => Aira.download(`/api/ai/sessions/${state.session}/export`, "aira-conversation.txt"), { kind: "ghost sm" });
    const headActions = h("div", { class: "btn-row ai-head-actions" }, rename, save);
    headActions.hidden = !state.session;

    put(body,
      h("div", { class: "ai-chat" },
        side,
        h("section", { class: "card ai-main" },
          h("header", { class: "ai-head" }, Aira.icon("ai"), headTitle, headActions, h("a", { class: "suite-pill info as-written ai-agents-on", href: "#/ai?tab=agents", text: `${on.length} agents on` })),
          statsHolder,
          log,
          h("div", { class: "ai-compose" }, mic, input, send),
          h("p", { class: "muted small ai-hint", text: "Press Enter to send · Shift+Enter for a new line. Monosage only uses what is in your apps." }))));
    draw();
    if (route.query.ask && !state.messages.length) { input.value = route.query.ask; ask(); } else if (!state.messages.length) input.focus();
  }

  // ---------------------------------------------------------------- agents
  async function configure(agent, rerender) {
    const consent = Aira.ui.select(Object.entries(CONSENT), agent.consent, { name: "consent" });
    const enabled = h("input", { type: "checkbox", role: "switch", checked: agent.enabled });
    const limit = h("input", { type: "number", name: "daily_limit", min: "0", max: "1000", step: "1", inputmode: "numeric", value: String(agent.daily_limit || 0) });
    const explain = h("p", { class: "muted small" });
    const levels = (await api("/agents", { silent: true })).consent_levels;
    const describe = () => { const level = levels.find((l) => l.id === consent.value); explain.textContent = level ? level.text : ""; };
    consent.addEventListener("change", describe);
    describe();
    Aira.modal({
      title: `Configure ${agent.name}`,
      body: h("div", { class: "suite-body" },
        h("p", { text: agent.summary }),
        h("label", { class: "life-toggle" }, enabled, h("span", {}, h("strong", { text: "Agent is on" }), h("span", { class: "muted", text: "When it is off, the chat does not read this app or act in it." }))),
        Aira.ui.field("Consent level", consent),
        explain,
        Aira.ui.field("Most actions alone in 24 hours", limit),
        h("p", { class: "muted small", text: `0 means no limit. When the agent reaches it, its next actions wait for you in Approvals. Good for agents that trade or spend.${agent.default_limit ? ` Until you change it, ${agent.name} uses its own limit of ${agent.default_limit}.` : ""}` }),
        h("h3", { class: "suite-h3", text: "What it can do" }),
        h("ul", { class: "life-lines" },
          h("li", { class: "life-line" }, h("span", { class: "life-line-text", text: `Read ${appName(agent.app)} to answer you` })),
          ...agent.actions.map((action) => h("li", { class: "life-line" }, h("span", { class: "life-line-text", text: action.label }), h("span", { class: `suite-pill as-written ${action.risk === "minor" ? "" : "warn"}`, text: action.risk === "minor" ? "Small action" : "Big action" }))))),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Save", kind: "primary", onClick: async () => {
          const body = { enabled: enabled.checked, consent: consent.value };
          // Only a limit the person changed is theirs; otherwise the agent's own default keeps applying.
          const wanted = limit.value === "" ? "0" : limit.value;
          if (wanted !== String(agent.daily_limit || 0)) body.daily_limit = wanted;
          await api(`/agents/${agent.id}`, { method: "PATCH", body });
          Aira.toast(`${agent.name} saved.`, "success");
          rerender();
        } },
      ],
    });
  }

  function activityList(ops, rerender) {
    if (!ops.length) return Aira.ui.empty("No activity yet", "When you chat with Monosage, what each agent reads and does shows up here.", h("a", { class: "btn secondary sm", href: "#/ai?tab=chat", text: "Start a conversation" }));
    return h("ul", { class: "suite-activity ai-activity-list" }, ...ops.map((op) => h("li", {},
      h("span", { class: `suite-dot ${op.status === "done" ? "" : "public"}`, "aria-hidden": "true" }),
      h("div", { class: "ai-activity-text" }, h("strong", { text: op.agent_name }), h("span", { text: ` ${op.summary}` }), h("div", { class: "muted small", text: `${Aira.fmt.ago(op.created_at)}${op.result ? ` · ${op.result}` : ""}` })),
      statusPill(op.status),
      op.can_undo && rerender ? S.button("Undo", async () => { if (await undoOp(op)) rerender(); }, { kind: "ghost sm" }) : null)));
  }

  async function agentsTab(body, rerender) {
    const data = await api("/agents");
    const k = data.kpis;
    hooks.open = (id) => { const agent = data.agents.find((a) => a.id === id); if (agent) configure(agent, rerender); };
    put(body,
      S.kpis([
        { label: "Active agents", value: `${k.active} / ${k.total}`, hint: k.total ? `${Math.round((k.active * 100) / k.total)}% of your agents` : "" },
        { label: "Total operations", value: k.ops.toLocaleString(), hint: `${k.ops_week} this week` },
        { label: "Avg success rate", value: k.success_rate === null ? "-" : `${k.success_rate}%`, tone: k.success_rate !== null && k.success_rate < 80 ? "warn" : "good" },
        { label: "Consent level", value: CONSENT[k.common_consent] || "-", hint: "Most common setting" },
        { label: "Helpful answers", value: k.helpful_percent === null || k.helpful_percent === undefined ? "-" : `${k.helpful_percent}%`, hint: k.rated ? `Of ${plural(k.rated, "answer", "answers")} you marked` : "Mark answers in the chat", tone: k.helpful_percent !== null && k.helpful_percent !== undefined && k.helpful_percent < 60 ? "warn" : "" },
      ]),
      S.section("Consent levels explained", h("div", { class: "ai-levels" }, ...data.consent_levels.map((level) => h("div", { class: "ai-level" }, h("strong", { text: level.label }), h("code", { text: level.id.toUpperCase() }), h("p", { class: "muted small", text: level.text }))))),
      S.section("Agents", h("p", { class: "muted", text: "One agent per app. Choose what each may do on its own." }),
        data.agents.length ? h("ul", { class: "ai-agents" }, ...data.agents.map((agent) => {
          const toggle = h("input", { type: "checkbox", role: "switch", checked: agent.enabled, "aria-label": `${agent.name} on` });
          toggle.addEventListener("change", async () => {
            try { await api(`/agents/${agent.id}`, { method: "PATCH", body: { enabled: toggle.checked } }); Aira.toast(`${agent.name} is ${toggle.checked ? "on" : "off"}.`, "success"); rerender(); } catch (error) { toggle.checked = !toggle.checked; Aira.reportError(error); }
          });
          return h("li", { class: `ai-agent ${agent.enabled ? "" : "off"}`, dataset: { id: agent.id } },
            h("span", { class: "ai-agent-icon", "aria-hidden": "true" }, Aira.icon(agent.app === "hub" ? "hub" : (Aira.APPS.find((a) => a.id === agent.app) || {}).icon || "ai")),
            h("div", { class: "ai-agent-name" }, h("strong", { text: agent.name }), h("span", { class: "muted small", text: appName(agent.app) })),
            h("div", { class: "ai-agent-stats" }, h("span", { text: plural(agent.ops, "operation", "operations") }), h("span", { class: "muted small", text: agent.success_rate === null ? "No actions yet" : `${agent.success_rate}% success` })),
            h("span", { class: "ai-agent-pills" },
              h("span", { class: `suite-pill as-written ${agent.consent === "auto_major" ? "warn" : agent.consent === "read_only" ? "" : "info"}`, text: CONSENT[agent.consent] || agent.consent }),
              agent.daily_limit && agent.consent !== "read_only" ? h("span", { class: "suite-pill as-written", text: `Up to ${agent.daily_limit} a day alone` }) : null),
            h("label", { class: "ai-switch" }, toggle, h("span", { text: agent.enabled ? "On" : "Off" })),
            S.button("Configure", () => configure(agent, rerender), { kind: "secondary sm" }));
        })) : Aira.ui.empty("No agents", "Agents come with the apps you have. Add an everyday app to get its agent.")),
      S.section("Recent agent activity", activityList(data.recent, rerender), h("div", { class: "btn-row" }, h("a", { class: "btn ghost sm", href: "#/ai?tab=activity", text: "See all activity" }))));
  }

  // ---------------------------------------------------------------- approvals
  async function approvalsTab(body, rerender) {
    const data = await api("/approvals");
    if (!data.ops.length) {
      body.append(Aira.ui.empty("Nothing waits for you", "Actions an agent may not take alone wait here until you approve them. Set each agent's consent level in Agents.",
        h("div", { class: "btn-row" }, h("a", { class: "btn secondary sm", href: "#/ai?tab=agents", text: "Choose consent levels" }), h("a", { class: "btn ghost sm", href: "#/ai?tab=chat", text: "Ask Monosage something" }))));
      return;
    }
    const decideAll = (approve) => S.button(approve ? "Approve all" : "Reject all", async () => {
      if (!(await Aira.confirm({ title: approve ? "Approve all" : "Reject all", message: `${approve ? "Run" : "Drop"} all ${data.ops.length} waiting actions?`, confirmLabel: approve ? "Approve all" : "Reject all", danger: !approve }))) return;
      let ok = 0;
      for (const op of data.ops) {
        try { const result = await api(`/ops/${op.id}/${approve ? "approve" : "reject"}`, { method: "POST", body: {}, silent: true }); if (result.op.status === (approve ? "done" : "rejected")) ok += 1; } catch { /* counted as not done */ }
      }
      Aira.toast(approve ? `${ok} of ${data.ops.length} done.` : `${ok} rejected.`, ok ? "success" : "");
      rerender();
      tick();
    }, { kind: approve ? "secondary sm" : "ghost sm" });
    body.append(S.section(`Waiting for your approval (${data.ops.length})`,
      data.ops.length > 1 ? h("div", { class: "btn-row" }, decideAll(true), decideAll(false)) : null,
      h("ul", { class: "ai-approvals" }, ...data.ops.map((op) => h("li", { class: "ai-action" },
        h("div", { class: "ai-action-main" }, h("strong", { text: op.summary }), h("span", { class: "muted small", text: `${op.agent_name} · ${op.risk === "major" ? "Big action" : "Small action"} · ${Aira.fmt.ago(op.created_at)}` })),
        h("div", { class: "btn-row" },
          S.button("Approve", async () => { const result = await api(`/ops/${op.id}/approve`, { method: "POST", body: {} }); Aira.toast(result.op.result, result.op.status === "done" ? "success" : ""); rerender(); tick(); }, { kind: "sm" }),
          S.button("Reject", async () => { await api(`/ops/${op.id}/reject`, { method: "POST", body: {} }); rerender(); tick(); }, { kind: "ghost sm" })))))));
  }

  // ---------------------------------------------------------------- routines
  function routineForm(routine, rerender) {
    const days = new Set((routine && routine.days) || [0, 1, 2, 3, 4, 5, 6]);
    const fields = [
      { name: "title", label: "Name", placeholder: "Morning brief", hint: "Leave empty to use what you ask." },
      { name: "prompt", label: "What should Monosage do?", type: "textarea", rows: 3, required: true, placeholder: "Give me my morning brief", hint: "A question (How are my goals going?) or a command (Add milk to the shopping list)." },
      { name: "at", label: "At", type: "time", required: true },
      { type: "heading", label: "On these days" },
      ...DAYS.map(([id, label], index) => ({ name: `day_${id}`, label, type: "checkbox", value: days.has(index) })),
      { name: "alert", label: "Show the answer when it is ready (and read it aloud in easy mode)", type: "checkbox", value: routine ? routine.alert : true },
    ];
    return S.edit({
      title: routine ? "Change routine" : "New routine",
      fields,
      values: routine ? { title: routine.title, prompt: routine.prompt, at: routine.at } : { at: "07:00" },
      submitLabel: routine ? "Save" : "Add routine",
      onSave: async (values) => {
        const chosen = DAYS.filter(([id]) => values[`day_${id}`]).map(([id]) => id);
        if (!chosen.length) throw new Error("Choose at least one day.");
        const payload = { title: values.title, prompt: values.prompt, at: values.at, days: chosen, alert: values.alert };
        if (routine) await api(`/routines/${routine.id}`, { method: "PATCH", body: payload });
        else await api("/routines", { method: "POST", body: payload });
        Aira.toast(routine ? "Routine saved." : "Routine added.", "success");
        rerender();
        tick();
      },
    });
  }

  async function routinesTab(body, rerender) {
    const data = await api("/routines");
    const add = S.button("New routine", () => routineForm(null, rerender), { kind: "primary" });
    hooks.new = () => routineForm(null, rerender);
    const intro = h("p", { class: "muted", text: `Monosage runs these for you at their time (your clock says ${data.clock}) while Monosage is open, shows the answer and puts it on Today. Commands follow each agent's consent level.` });
    const list = data.routines.length ? h("ul", { class: "ai-routines" }, ...data.routines.map((routine) => {
      const toggle = h("input", { type: "checkbox", role: "switch", checked: routine.enabled, "aria-label": `${routine.title} on` });
      toggle.addEventListener("change", async () => {
        try { await api(`/routines/${routine.id}`, { method: "PATCH", body: { enabled: toggle.checked } }); Aira.toast(`${routine.title} is ${toggle.checked ? "on" : "off"}.`, "success"); rerender(); } catch (error) { toggle.checked = !toggle.checked; Aira.reportError(error); }
      });
      const last = routine.last_run_on ? (routine.ran_today ? "Ran today" : `Last ran ${routine.last_run_on}`) : "Not run yet";
      return h("li", { class: `ai-routine ${routine.enabled ? "" : "off"}`, dataset: { id: routine.id } },
        h("div", { class: "ai-routine-when" }, h("strong", { text: routine.at }), h("span", { class: "muted small", text: routine.days_label })),
        h("div", { class: "ai-routine-main" },
          h("strong", { text: routine.title }),
          h("span", { class: "muted small", text: `"${routine.prompt}" · ${last}` }),
          routine.last_answer ? h("p", { class: "ai-routine-answer small", text: routine.last_answer }) : null),
        h("label", { class: "ai-switch" }, toggle, h("span", { text: routine.enabled ? "On" : "Off" })),
        h("div", { class: "btn-row ai-routine-actions" },
          S.button("Run now", async () => {
            Aira.toast("Monosage is working on it…");
            const result = await api(`/routines/${routine.id}/run`, { method: "POST", body: { seen: true } });
            tick();
            Aira.router.go(["ai"], { tab: "chat", session: result.session_id });
          }, { kind: "secondary sm" }),
          routine.session_id ? h("a", { class: "btn ghost sm", href: `#/ai?tab=chat&session=${routine.session_id}`, text: "Read answers" }) : null,
          S.button("Change", () => routineForm(routine, rerender), { kind: "ghost sm" }),
          S.button("Delete", async () => { if (!(await S.confirmDelete(`the routine "${routine.title}"`))) return; await api(`/routines/${routine.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" })));
    })) : Aira.ui.empty("No routines yet", "Add one, or start from an idea below: Monosage will give you your morning brief at 7 every day.");
    const have = new Set(data.routines.map((r) => r.prompt.toLowerCase()));
    const ideas = data.suggested.filter((idea) => !have.has(idea.prompt.toLowerCase()));
    put(body,
      S.section("Your routines", intro, h("div", { class: "btn-row" }, add), list),
      ideas.length ? S.section("Ideas", h("ul", { class: "ai-ideas" }, ...ideas.map((idea) => h("li", { class: "ai-idea" },
        h("div", { class: "ai-routine-main" }, h("strong", { text: idea.title }), h("span", { class: "muted small", text: `"${idea.prompt}" at ${idea.at}, ${idea.days.length === 7 ? "every day" : idea.days.map((d) => DAYS[d][1]).join(", ")}` })),
        S.button("Add", async () => { await api("/routines", { method: "POST", body: idea }); Aira.toast(`${idea.title} added.`, "success"); rerender(); tick(); }, { kind: "secondary sm" }))))) : null);
  }

  // ---------------------------------------------------------------- memory
  async function memoryTab(body, route, rerender) {
    const q = route.query.q || "";
    const data = await api(`/memories${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    const box = h("textarea", { class: "ai-memory-input", rows: 2, maxlength: 300, placeholder: "For example: I am vegetarian. My daughter's school is Oakfield.", "aria-label": "Something for Monosage to remember" });
    const add = S.button("Remember this", async () => {
      const text = box.value.trim();
      if (!text) { box.focus(); return; }
      const result = await api("/memories", { method: "POST", body: { text } });
      Aira.toast(result.memory && result.memory.existing ? `Monosage already remembers that: "${result.memory.text}"` : "Monosage will remember that.", "success");
      rerender();
    }, { kind: "primary" });
    box.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); add.click(); } });
    hooks.new = () => { box.scrollIntoView({ block: "center" }); box.focus(); };
    const search = h("input", { type: "search", placeholder: "Search memories", "aria-label": "Search memories", value: q });
    const go = () => Aira.router.go(["ai"], { tab: "memory", q: search.value.trim() });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") go(); });
    search.addEventListener("search", go);
    const edit = (item) => S.edit({
      title: "Change memory",
      fields: [{ name: "text", label: "What Monosage remembers", type: "textarea", rows: 3, required: true }],
      values: { text: item.text },
      onSave: async (values) => { await api(`/memories/${item.id}`, { method: "PATCH", body: values }); Aira.toast("Saved.", "success"); rerender(); },
    });
    const list = data.memories.length ? h("ul", { class: "ai-memories" }, ...data.memories.map((item) => h("li", { class: "ai-memory", dataset: { id: item.id } },
      h("div", { class: "ai-routine-main" }, h("span", { class: "ai-memory-text", text: item.text }), h("span", { class: "muted small", text: `Added ${Aira.fmt.ago(item.created_at)}` })),
      h("div", { class: "btn-row" },
        S.button("Change", () => edit(item), { kind: "ghost sm" }),
        S.button("Forget", async () => {
          if (!(await Aira.confirm({ title: "Forget this", message: `Forget "${item.text}"?`, confirmLabel: "Forget", danger: true }))) return;
          await api(`/memories/${item.id}`, { method: "DELETE" });
          rerender();
        }, { kind: "ghost sm" })))))
      : Aira.ui.empty(q ? "Nothing matches" : "Monosage remembers nothing yet", q ? "Try other words." : "Add something above, or say \"Remember that ...\" in the chat.");
    const clearAll = data.memories.length && !q ? S.button("Forget everything", async () => {
      if (!(await Aira.confirm({ title: "Forget everything", message: "Forget everything you asked Monosage to remember? This cannot be undone.", confirmLabel: "Forget everything", danger: true }))) return;
      const result = await api("/memories", { method: "DELETE" });
      Aira.toast(`Forgot ${plural(result.deleted, "thing", "things")}.`, "success");
      rerender();
    }, { kind: "ghost sm" }) : null;
    put(body,
      S.section("Tell Monosage something to remember",
        h("p", { class: "muted", text: "Monosage keeps these in mind for every answer, like your diet, your family's names or how you like answers. Only you see them." }),
        h("div", { class: "ai-memory-add" }, box, add)),
      S.section(`What Monosage remembers (${data.memories.length} of ${data.limit})`,
        h("div", { class: "btn-row ai-filters" }, search, clearAll),
        list));
  }

  // ---------------------------------------------------------------- earn
  const PACE = { on_track: ["On track", "good"], behind: ["Behind", "warn"], late: ["Past its date", "bad"], reached: ["Reached", "good"] };
  function paceLine(goal) {
    const pace = goal.pace;
    if (!pace || !PACE[pace.state]) return null;
    const money = (cents) => S.money(cents, pace.currency);
    const [label, tone] = PACE[pace.state];
    const text = pace.state === "late" ? `${money(pace.remaining_cents)} still to go.`
      : pace.state === "reached" ? "You reached it."
      : `Needs ${money(pace.need_week_cents)} a week. Lately your apps earned ${money(pace.recent_week_cents)} a week. At that pace: ${money(pace.projected_cents)} by ${goal.target_date}.`;
    return h("div", { class: "ai-pace" }, h("span", { class: `suite-pill as-written ${tone}`, text: label }), h("span", { class: "small", text }));
  }

  async function earnTab(body, rerender) {
    const data = await api("/earn");
    const askButton = h("a", { class: "btn primary", href: `#/ai?tab=chat&ask=${encodeURIComponent("How much did I earn this month and how can I earn more?")}`, text: "Ask Monosage how to earn more" });
    const ways = data.ways.length ? h("ul", { class: "ai-ways" }, ...data.ways.map((way) => h("li", { class: "ai-way" },
      h("strong", { text: way.name }), h("span", { class: "muted small", text: way.text }), h("a", { class: "btn ghost sm", href: way.link, text: `Open ${way.name}` })))) : Aira.ui.empty("No earning apps yet", "Add a business app, Videos or Stocks to start earning.");
    const fromAgents = data.from_agents.length ? S.section("From your apps", h("ul", { class: "life-lines" }, ...data.from_agents.flatMap((entry) => entry.lines.map((line) => h("li", { class: "life-line" }, h("span", { class: "life-line-text", text: line }), h("span", { class: "muted small", text: entry.name })))))) : null;
    if (!data.may_see) {
      put(body,
        S.section("Ways to earn with your apps", h("p", { class: "muted", text: "The money your apps earn is for the adults who run the account. Here are the ways these apps help people earn." }), ways),
        fromAgents);
      return;
    }
    const money = (cents) => S.money(cents, data.currency);
    const change = data.change_percent === null ? "No sales last month" : `${data.change_percent > 0 ? "+" : ""}${data.change_percent}% on last month`;
    const setGoal = () => S.edit({
      title: "Set an earning goal",
      fields: [
        { name: "target", label: `How much do you want to earn (${data.currency})?`, type: "number", required: true, min: 1, step: "any", placeholder: "50000" },
        { name: "target_date", label: "By when?", type: "date", hint: "Optional" },
        { name: "title", label: "Name", placeholder: "Earn 50,000 by December", hint: "Optional" },
      ],
      submitLabel: "Set goal",
      onSave: async (values) => {
        if (!(Number(values.target) > 0)) throw new Error("Enter an amount above 0, for example 50000.");
        const result = await api("/earn/goals", { method: "POST", body: { target: values.target === null ? "" : String(values.target), target_date: values.target_date, title: values.title } });
        const outcome = result.outcome;
        Aira.toast(outcome.status === "done" ? outcome.result : outcome.status === "pending" ? "Waiting for your approval in Approvals." : outcome.result, outcome.status === "done" ? "success" : "");
        rerender();
        tick();
      },
    });
    const goalAdd = data.goals === null ? null : S.button("Set an earning goal", setGoal, { kind: "secondary" });
    if (goalAdd) hooks.new = setGoal;
    const goals = data.goals === null
      ? h("p", { class: "muted", text: "Add the Life app to keep earning goals that count what your apps earn." })
      : data.goals.length ? h("ul", { class: "ai-goals" }, ...data.goals.map((goal) => h("li", { class: "ai-goal" },
        h("div", { class: "ai-goal-head" }, h("strong", { text: goal.title }), h("span", { class: "muted small", text: `${S.money(goal.current_cents, goal.currency || data.currency)} of ${S.money(goal.target_cents, goal.currency || data.currency)}${goal.target_date ? ` · by ${goal.target_date}` : ""}` })),
        h("div", { class: "ai-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(goal.progress), "aria-label": `${goal.title}: ${goal.progress}%` }, h("span", { style: { width: `${Math.max(0, Math.min(100, goal.progress))}%` } })),
        h("span", { class: "small", text: goal.status === "completed" ? "Reached. Well done!" : `${goal.progress}% there` }),
        paceLine(goal),
        h("a", { class: "btn ghost sm", href: "#/journey", text: "Open in Life" }))))
        : Aira.ui.empty("No earning goal yet", "Set one: it counts what your apps earn from today, by itself.");
    const steps = data.steps.length ? h("ul", { class: "ai-steps" }, ...data.steps.map((step) => h("li", { class: "ai-step" },
      h("div", { class: "ai-routine-main" }, h("strong", { text: step.label }), h("span", { class: "muted small", text: `${step.name}${step.hint ? ` · ${step.hint}` : ""}` })),
      h("a", { class: "btn secondary sm", href: step.link, text: `Open ${step.name}` })))) : h("p", { class: "muted", text: "Your business apps are set up. Keep going!" });
    const byApp = data.by_app.length ? S.bars(data.by_app.map((a) => ({ label: a.name, value: a.cents })), { format: money }) : h("p", { class: "muted", text: "Nothing earned this month yet." });
    put(body,
      S.kpis([
        { label: "Earned this month", value: money(data.this_month_cents), hint: change, tone: data.change_percent !== null && data.change_percent < 0 ? "warn" : data.this_month_cents > 0 ? "good" : "" },
        { label: "Last month", value: money(data.last_month_cents) },
        { label: "All time", value: money(data.total_cents), hint: plural(data.payments, "payment", "payments") },
        { label: "Emails waiting", value: String(data.outbox_pending), hint: data.outbox_pending ? "Approve them in the Business hub outbox" : "Nothing waits", tone: data.outbox_pending ? "warn" : "" },
      ]),
      h("div", { class: "btn-row" }, askButton, goalAdd, data.outbox_pending ? h("a", { class: "btn ghost", href: "#/hub", text: "Open the outbox" }) : null),
      S.grid(
        S.section("Earning goals", goals),
        S.section("This month by app", byApp)),
      S.section("Next steps to earn more", steps),
      fromAgents,
      S.section("Ways to earn with your apps", ways));
  }

  // ---------------------------------------------------------------- activity
  async function activityTab(body, route, rerender) {
    const q = route.query;
    const params = new URLSearchParams({ limit: "300" });
    for (const key of ["agent", "status", "kind", "q"]) if (q[key]) params.set(key, q[key]);
    const [data, agents] = await Promise.all([api(`/ops?${params}`), api("/agents", { silent: true })]);
    const go = (changes) => Aira.router.go(["ai"], { ...q, tab: "activity", ...changes });
    const agentSelect = Aira.ui.select([["", "Every agent"], ...agents.agents.map((a) => [a.id, a.name])], q.agent || "", { "aria-label": "Agent" });
    agentSelect.addEventListener("change", () => go({ agent: agentSelect.value }));
    const statusSelect = Aira.ui.select([["", "Any result"], ...Object.entries(STATUS_LABELS)], q.status || "", { "aria-label": "Result" });
    statusSelect.addEventListener("change", () => go({ status: statusSelect.value }));
    const kindSelect = Aira.ui.select([["", "Reads and actions"], ["read", "Reads"], ["action", "Actions"]], q.kind || "", { "aria-label": "Kind" });
    kindSelect.addEventListener("change", () => go({ kind: kindSelect.value }));
    const search = h("input", { type: "search", placeholder: "Search what agents did", "aria-label": "Search activity", value: q.q || "" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") go({ q: search.value.trim() }); });
    search.addEventListener("search", () => go({ q: search.value.trim() }));
    const exportParams = new URLSearchParams();
    for (const key of ["agent", "status", "kind", "q"]) if (q[key]) exportParams.set(key, q[key]);
    const filtered = q.agent || q.status || q.kind || q.q;
    // The empty state stands outside the table and the phone list, so a phone sees it too.
    const empty = filtered ? Aira.ui.empty("Nothing matches", "Try another filter or search.", h("a", { class: "btn secondary sm", href: "#/ai?tab=activity", text: "Show all activity" }))
      : activityList([], null);
    put(body,
      h("div", { class: "btn-row jr-filters ai-filters" }, search, agentSelect, statusSelect, kindSelect, S.button("Export CSV", () => Aira.download(`/api/ai/export/ops${exportParams.toString() ? `?${exportParams}` : ""}`, "agent-activity.csv"), { kind: "ghost" })),
      data.ops.length ? h("div", { class: "ai-activity-table" }, S.table({
        caption: "Agent activity",
        columns: [
          { key: "created_at", label: "When", render: (op) => Aira.fmt.datetime(op.created_at) },
          { key: "agent_name", label: "Agent" },
          { key: "kind", label: "Kind", render: (op) => (op.kind === "read" ? "Read" : "Action") },
          { key: "summary", label: "What" },
          { key: "status", label: "Result", render: (op) => statusPill(op.status) },
          { key: "result", label: "Details" },
          { key: "id", label: "", render: (op) => (op.can_undo ? S.button("Undo", async () => { if (await undoOp(op)) rerender(); }, { kind: "ghost sm" }) : "") },
        ],
        rows: data.ops,
      })) : empty,
      data.ops.length ? h("div", { class: "ai-activity-cards" }, activityList(data.ops, rerender)) : null);
  }

  // ---------------------------------------------------------------- search results (Enter in the search across the top)
  // The lists the search reads; kept a few seconds while typing, read again after each page draw.
  const found = { at: 0, data: null };
  async function searchLists() {
    if (found.data && Date.now() - found.at < 15000) return found.data;
    const [memories, routines, agents] = await Promise.all([api("/memories", { silent: true }), api("/routines", { silent: true }), api("/agents", { silent: true })]);
    found.data = { memories: memories.memories, routines: routines.routines, agents: agents.agents };
    found.at = Date.now();
    return found.data;
  }
  const norm = (text) => String(text || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  const agentIcon = (agent) => (agent.app === "hub" ? "hub" : (Aira.APPS.find((a) => a.id === agent.app) || {}).icon || "ai");
  // Everything of the person's that matches: conversations (names and messages, from the server), memories, routines
  // and agents (from the lists above), each with the address that opens it.
  async function searchAll(query) {
    const words = norm(query).split(/\s+/).filter(Boolean);
    const hit = (...texts) => { const all = norm(texts.join(" ")); return words.every((word) => all.includes(word)); };
    const [sessions, lists] = await Promise.all([api(`/sessions?q=${encodeURIComponent(query)}`, { silent: true }), searchLists()]);
    return {
      sessions: sessions.sessions.map((x) => ({ label: x.title, sub: `Conversation · ${Aira.fmt.ago(x.updated_at)}`, icon: "social", href: `#/ai?tab=chat&session=${x.id}` })),
      memories: lists.memories.filter((m) => hit(m.text)).map((m) => ({ label: m.text, sub: `Memory · added ${Aira.fmt.ago(m.created_at)}`, icon: "learn", href: `#/ai?tab=memory&open=${m.id}` })),
      routines: lists.routines.filter((r) => hit(r.title, r.prompt)).map((r) => ({ label: r.title, sub: `Routine · ${r.at}, ${r.days_label}${r.enabled ? "" : " · off"}`, icon: "interviews", href: `#/ai?tab=routines&open=${r.id}` })),
      agents: lists.agents.filter((a) => hit(a.name, appName(a.app))).map((a) => ({ label: a.name, sub: `Agent · ${appName(a.app)} · ${a.enabled ? CONSENT[a.consent] || a.consent : "off"}`, icon: agentIcon(a), href: `#/ai?tab=agents&open=${a.id}` })),
    };
  }

  async function resultsTab(body, route) {
    const q = String(route.query.q || "").trim();
    if (!q) { body.append(Aira.ui.empty("Search AI", "Type in the search box above: conversations, memories, routines, agents and activity.")); return; }
    const short = q.length > 60 ? `${q.slice(0, 60)}…` : q;
    const askLink = h("a", { class: "btn primary", href: `#/ai?tab=chat&ask=${encodeURIComponent(q)}` }, Aira.icon("ai"), h("span", { text: `Ask Monosage: “${short}”` }));
    const [hits, ops] = await Promise.all([searchAll(q), api(`/ops?${new URLSearchParams({ q, limit: "20" })}`, { silent: true })]);
    const group = (title, rows) => (rows.length ? S.section(`${title} (${rows.length})`,
      h("ul", { class: "ai-hits" }, ...rows.map((row) => h("li", {},
        h("a", { class: "ai-hit", href: row.href }, h("span", { class: "ai-hit-icon", "aria-hidden": "true" }, Aira.icon(row.icon)), h("span", { class: "ai-hit-text" }, h("strong", { text: row.label }), h("span", { class: "muted small", text: row.sub }))))))) : null);
    const total = hits.sessions.length + hits.memories.length + hits.routines.length + hits.agents.length + ops.ops.length;
    put(body,
      h("div", { class: "ai-results-head" }, h("p", { class: "ai-results-for", text: total ? `${plural(total, "match", "matches")} for “${short}”` : `Nothing in AI matches “${short}”` }), askLink),
      total ? null : Aira.ui.empty("No conversation, memory, routine or agent has these words", "Try other words, or ask Monosage: it reads your apps to answer.", h("a", { class: "btn secondary sm", href: "#/ai?tab=chat", text: "Open the chat" })),
      group("Conversations", hits.sessions),
      group("Memories", hits.memories),
      group("Routines", hits.routines),
      group("Agents", hits.agents),
      ops.ops.length ? S.section(`Activity (${ops.ops.length}${ops.ops.length >= 20 ? "+" : ""})`, activityList(ops.ops.slice(0, 6), null),
        h("div", { class: "btn-row" }, h("a", { class: "btn ghost sm", href: `#/ai?tab=activity&q=${encodeURIComponent(q)}`, text: "See all in Activity" }))) : null);
  }

  // ---------------------------------------------------------------- in the Aira frame
  const TABS = [
    { id: "chat", label: "Chat", icon: "social", about: "Ask about your apps, or ask Monosage to do something. You can undo what it does." },
    { id: "agents", label: "Agents", icon: "ai", about: "One agent per app. Choose what each may do on its own." },
    { id: "approvals", label: "Approvals", icon: "applications", about: "Actions an agent may not take alone wait here for your yes." },
    { id: "routines", label: "Routines", icon: "interviews", about: "Questions and commands Monosage runs for you at a set time." },
    { id: "memory", label: "Memory", icon: "learn", about: "What Monosage keeps in mind for every answer. Only you see it." },
    { id: "earn", label: "Earn", icon: "stocks", about: "What your apps earn, your earning goals and ways to earn more." },
    { id: "activity", label: "Activity", icon: "analytics", about: "Every read and action your agents took, with Undo." },
  ];
  const NEW_TAB = { chat: "chat", routine: "routines", memory: "memory", goal: "earn" };
  // An earning goal counts the account's earnings: adults here, and the staff who see them in an organization
  // (aira.suite.journey.service.may_count_earnings); the Life app keeps the goals.
  function mayEarnGoal() {
    if (Aira.apps.describe("journey").enabled === false) return false;
    const cloud = Aira.cloud;
    if (cloud && cloud.me && typeof cloud.role === "function") return ["owner", "admin", "coach", "auditor"].includes(cloud.role());
    const me = Aira.life && Aira.life.me;
    return !me || !me.age_group || ["adult", "senior"].includes(me.age_group);
  }
  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  // "+ New": what people start most here, each opening the same form as the button on its tab.
  Aira.appNew.ai = () => [
    { label: "New conversation", icon: "social", href: "#/ai?tab=chat&new=chat" },
    { label: "New routine", icon: "interviews", href: "#/ai?tab=routines&new=routine" },
    { label: "Something to remember", icon: "learn", href: "#/ai?tab=memory&new=memory" },
    mayEarnGoal() ? { label: "Earning goal", icon: "stocks", href: "#/ai?tab=earn&new=goal" } : null,
  ].filter(Boolean);
  Aira.appSearch.ai = {
    placeholder: "Search chats, memories and routines",
    // Up to six, each kind getting a place: conversations first, then memories, routines and agents.
    async suggest(query) {
      const text = query.trim();
      if (!text) return [];
      const hits = await searchAll(text);
      const first = [...hits.sessions.slice(0, 2), ...hits.memories.slice(0, 2), ...hits.routines.slice(0, 1), ...hits.agents.slice(0, 1)];
      const rest = [...hits.sessions.slice(2), ...hits.memories.slice(2), ...hits.routines.slice(1), ...hits.agents.slice(1)];
      return [...first, ...rest].slice(0, 6);
    },
    // Enter: every match, activity included, on a results page with a way to ask Aira the same words.
    run(query) { Aira.router.go(["ai"], { tab: "results", q: query.trim() }); },
  };

  let pending = null; // what "+ New" or the search asked for: { new, open }
  const hooks = {}; // set by the drawn tab: new() opens its create form, open(id) opens a record
  function pick(node) {
    if (!node) return;
    node.classList.add("ai-picked");
    node.scrollIntoView({ block: "center" });
    const control = node.querySelector("button, a, input");
    if (control) control.focus({ preventScroll: true });
  }

  Aira.views.ai = async (main, route) => {
    // "+ New" and the search ask with ?new= or ?open=: remember the ask, take it out of the address (Back or a refresh
    // must not ask again) and draw the tab without it; the drawn tab then answers it.
    const query = route.query || {};
    if (query.new || query.open) {
      pending = { new: query.new || "", open: query.open || "" };
      const rest = { ...query };
      delete rest.new;
      delete rest.open;
      if (!rest.tab) rest.tab = NEW_TAB[pending.new] || "chat";
      Aira.router.go(["ai"], rest, { replace: true });
      return;
    }
    const ask = pending;
    pending = null;
    found.at = 0;
    const tab = query.tab === "results" ? "results" : TABS.some((t) => t.id === query.tab) ? query.tab : "chat";
    const current = tab === "results" ? { label: "Search results", about: "Your conversations, memories, routines, agents and activity." } : TABS.find((t) => t.id === tab);
    const newChat = tab === "chat" ? S.button("New conversation", () => Aira.router.go(["ai"], { tab: "chat" }), { kind: "secondary" }) : null;
    if (newChat) newChat.classList.add("ai-new-chat");
    const body = S.page(main, { title: current.label, subtitle: current.about, actions: [newChat] });
    body.classList.add("ai-page");
    S.tabs(body, route, "ai", TABS, tab);
    const panel = h("div", { class: "suite-body" }, Aira.ui.skeleton(4));
    body.append(panel);
    const render = async () => {
      for (const key of Object.keys(hooks)) delete hooks[key];
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "agents") await agentsTab(holder, render);
        else if (tab === "approvals") await approvalsTab(holder, render);
        else if (tab === "routines") await routinesTab(holder, render);
        else if (tab === "memory") await memoryTab(holder, route, render);
        else if (tab === "earn") await earnTab(holder, render);
        else if (tab === "activity") await activityTab(holder, route, render);
        else if (tab === "results") await resultsTab(holder, route);
        else await chatTab(holder, route);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
    if (!ask || !main.isConnected) return;
    if (ask.new && hooks.new) hooks.new();
    else if (ask.new === "goal") Aira.toast(mayEarnGoal() ? "Earning goals need the Life app. Add it from the apps." : "Earning goals are for the adults who run the account.");
    else if (ask.open && hooks.open) hooks.open(ask.open);
    else if (ask.open) pick(panel.querySelector(`[data-id="${CSS.escape(ask.open)}"]`));
  };
})();

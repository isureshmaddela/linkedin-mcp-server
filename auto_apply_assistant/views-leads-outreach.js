/* Aira — Leads outreach: the email outbox, the sender settings, the do-not-contact list, and the call assist.
 *
 * Loaded after views-leads.js, which provides the shared pieces as Aira.leads.kit and calls back into the two
 * objects defined here:
 *
 * - Aira.leads.outreach: render() builds the Outreach tab (?tab=outreach); writeEmails() asks the model for one email
 *   draft per lead (nothing is sent).
 * - Aira.leads.extras: drawerSections() gives the lead drawer its “What they need”, “Website check”, “Call” and “Email”
 *   sections; followUps() is the “Follow-ups due” badge on the Leads tab.
 *
 * The rules this screen exists to make plain:
 * - An email is sent only after a person approves that message, through the person's own mail server, one at a time with
 *   a pause, up to a daily cap. Every message carries the sender's name, postal address and an opt-out line, and a
 *   do-not-contact list is checked before every send.
 * - Aira never dials. It prepares a call (a talk track, the number, a link that opens Google Voice's dialler for a human
 *   to press Call, whether it is a sensible hour there) and records the outcome. No auto-dialer, no recordings, no
 *   synthetic voice.
 *
 * Everything the server sends goes on the page with textContent (the h() helper); nothing is parsed as HTML.
 */
"use strict";
(() => {
  const root = typeof window !== "undefined" ? window : globalThis;
  const Aira = root.Aira;
  if (!Aira || !Aira.leads || !Aira.leads.kit || typeof document === "undefined") return;
  const K = Aira.leads.kit;
  const { h, api, fmt, ui, toast, BASE, btn, chip, ico, fill, busy, externalLink, textField, copyText, plural, trim, link, loadCatalog, liveOffered, logic } = K;
  const { OUTBOX_STATUSES, OUTCOME_FALLBACK, SEVERITY_TONE, authorLabel, outboxState, settingsPayload, problemsText, summarizeDrafts, needsOf, serviceLabel } = logic;
  const STATUS_TONE = { draft: "", approved: "ok", sent: "ok", failed: "danger", rejected: "", skipped: "warn" };
  const STATUS_LABEL = { draft: "Draft", approved: "Approved", sent: "Sent", failed: "Failed", rejected: "Rejected", skipped: "Skipped" };

  const message = (error) => (error && error.message ? String(error.message) : "Something went wrong.");
  const tonedChip = (text, tone) => h("span", { class: `ld-chip ${tone || ""}`.trim(), text });

  // ===========================================================================
  // Writing emails (from the Leads tab and the lead drawer)
  // ===========================================================================

  function showDraftResult(out) {
    const s = summarizeDrafts(out);
    Aira.modal({
      title: "Emails written",
      body: h("div", { class: "ld-form", id: "ldDraftResult" },
        h("p", {}, h("strong", { text: s.headline })),
        s.hint ? h("p", { class: "muted", text: s.hint }) : null,
        s.skipped.length ? h("ul", { class: "ld-skipped", "aria-label": "Skipped leads" }, ...s.skipped.map((x) => h("li", {}, h("strong", { text: x.name }), `: ${x.reason}`))) : null),
      actions: [{ label: "Close", kind: "ghost" }, { label: "Open the Outbox", kind: "primary", onClick: () => { location.hash = link("outreach"); } }],
    });
  }

  async function writeEmails(ids, extra = {}) {
    const services = liveOffered();
    const out = await api(`${BASE}/outbox/drafts`, { method: "POST", body: { lead_ids: ids, ...(services.length ? { services } : {}), ...extra }, expect: 201 });
    showDraftResult(out);
    return out;
  }

  // ===========================================================================
  // The lead drawer: needs, website check, call, email
  // ===========================================================================

  const scripts = new Map(); // lead id -> the last talk track, kept while the drawer repaints
  const CALL_LOGGED = "aira:leads-call-logged";

  function needsBlock(lead, helpers) {
    const offered = helpers.offered();
    const catalog = helpers.catalog();
    const findings = ((lead.enrichment || {}).audit || {}).findings || [];
    // the audit names the exact finding behind each service, which reads better than the first weak-site signal
    const needs = needsOf(lead, offered, catalog).map((n) => { const f = findings.find((x) => x.service === n.service); return f ? { ...n, reason: f.label } : n; });
    return h("section", { class: "ld-block ld-needs", "aria-label": "What they need", id: "ldNeeds" },
      h("h3", { text: "What they need" }),
      !offered.length
        ? h("p", { class: "muted", text: "Choose the services you offer (Find tab > Services I offer) to see which of them this business needs." })
        : needs.length
          ? h("ul", { class: "ld-needlist" }, ...needs.map((n) => h("li", {}, tonedChip("Needs", "hot"), " ", h("strong", { text: n.label }), " — ", n.reason)))
          : h("p", { class: "muted", text: "Nothing on this lead points to a need for the services you offer. Reading its website can show more." }));
  }

  function auditBlock(lead, helpers) {
    const audit = (lead.enrichment || {}).audit || {};
    const findings = audit.findings || [];
    const catalog = helpers.catalog();
    const run = btn(audit.url ? "Check again" : "Check the website", { icon: "search", sm: true, disabled: !lead.website, title: lead.website ? "" : "Add the company's website first", onClick: (event) => busy(event.currentTarget, async () => {
      const out = await api(`${BASE}/items/${lead.id}/enrich`, { method: "POST", body: {} });
      helpers.update(out.lead);
      toast("Website checked.", "success");
    }) });
    return h("section", { class: "ld-block ld-audit", "aria-label": "Website check", id: "ldAudit" },
      h("h3", { text: "Website check" }),
      h("p", { class: "muted", text: "A plain look at the home page: what anyone can see is missing in its source. These are observations, not a score, and the checks are repeatable. Monosage reads only public pages and respects robots.txt." }),
      !lead.website ? h("p", { class: "muted", text: "This lead has no website to check." }) : null,
      audit.url ? h("p", { class: "ld-audit-summary" }, h("strong", { text: audit.summary || "" }), h("span", { class: "muted", text: ` · answered in ${audit.seconds >= 1 ? `${audit.seconds} s` : "under 1 s"}${audit.builder ? ` · built with ${audit.builder}` : ""}` })) : null,
      findings.length ? h("ul", { class: "ld-findings", "aria-label": "Findings" }, ...findings.map((f) => h("li", {},
        tonedChip(f.severity, SEVERITY_TONE[f.severity] || ""), " ", h("strong", { text: f.label }),
        f.detail ? h("span", { class: "muted", text: ` — ${f.detail}` }) : null, " ",
        tonedChip(serviceLabel(catalog, f.service), "ok")))) : null,
      h("div", { class: "btn-row" }, run));
  }

  function scoreText(script) {
    return ["Opener", script.opener, "", "Pitch", script.pitch, "", "Questions", ...(script.questions || []).map((q, i) => `${i + 1}. ${q}`), "", "If they say…", ...(script.objections || []).map((o) => `“${o.objection}” → ${o.response}`), "", "Close", script.close, "", "Voicemail", script.voicemail].join("\n");
  }

  function scriptView(script) {
    const part = (title, text) => h("div", { class: "ld-script-part" }, h("div", { class: "ld-script-head" }, h("strong", { text: title }), btn("Copy", { icon: "copy", sm: true, kind: "ghost", aria: `Copy ${title.toLowerCase()}`, onClick: () => copyText(text, `${title} copied`) })), h("p", { text }));
    return h("div", { class: "ld-script", id: "ldScript" },
      h("p", { class: "muted", text: script.source === "model" ? "Written by Ollama from this lead's facts. Read it and make it yours." : "Filled in from a template with this lead's facts (Ollama was not available). Read it and make it yours." }),
      part("Opener", script.opener), part("Pitch", script.pitch),
      h("div", { class: "ld-script-part" }, h("div", { class: "ld-script-head" }, h("strong", { text: "Questions to ask" }), btn("Copy", { icon: "copy", sm: true, kind: "ghost", aria: "Copy questions", onClick: () => copyText((script.questions || []).map((q, i) => `${i + 1}. ${q}`).join("\n"), "Questions copied") })), h("ol", {}, ...(script.questions || []).map((q) => h("li", { text: q })))),
      h("div", { class: "ld-script-part" }, h("div", { class: "ld-script-head" }, h("strong", { text: "If they say…" }), btn("Copy", { icon: "copy", sm: true, kind: "ghost", aria: "Copy answers to objections", onClick: () => copyText((script.objections || []).map((o) => `“${o.objection}” → ${o.response}`).join("\n"), "Answers copied") })), h("dl", { class: "ld-objections" }, ...(script.objections || []).flatMap((o) => [h("dt", { text: o.objection }), h("dd", { text: o.response })]))),
      part("Close", script.close), part("If it goes to voicemail", script.voicemail),
      btn("Copy the whole talk track", { icon: "copy", sm: true, onClick: () => copyText(scoreText(script), "Talk track copied") }));
  }

  function callBlock(lead, helpers) {
    const box = h("section", { class: "ld-block ld-call", "aria-label": "Call", id: "ldCall" }, h("h3", { text: "Call" }));
    const rule = h("p", { class: "ld-nodial", id: "ldNoDial" }, ico("shield"), h("span", {}, h("strong", { text: "Monosage never dials. You place the call." }),
      " Monosage has no auto-dialer: it never places a call, plays a recording or speaks with a synthetic voice. Google Voice's policy forbids automated calling, and in many places an AI-voice marketing call needs the called person's prior written consent. Monosage writes the talk track and keeps the record; you press Call."));
    const body = h("div", { class: "ld-call-body" }, ui.skeleton(2));
    box.append(rule, body);

    async function load() {
      let dial; let history;
      try {
        [dial, history] = await Promise.all([api(`${BASE}/items/${lead.id}/dial`, { silent: true }), api(`${BASE}/items/${lead.id}/calls`, { silent: true })]);
      } catch (error) { fill(body, h("p", { class: "ld-warning", text: `The call panel could not load: ${message(error)}` })); return; }
      paint(dial.dial, history.calls);
    }

    function paint(dial, calls) {
      const blocked = Boolean(dial.blocked);
      const when = dial.window || {};
      const windowTone = when.ok === true ? "ok" : when.ok === false ? "warn" : "";
      const windowLabel = when.ok === true ? "A good time to call" : when.ok === false ? "Outside calling hours" : "Check the local time";
      const result = h("div", { class: "ld-script-box", id: "ldScriptBox" });
      if (scripts.has(lead.id)) result.append(scriptView(scripts.get(lead.id)));
      const makeScript = btn("Make a talk track", { icon: "spark", kind: "", onClick: (event) => busy(event.currentTarget, async () => {
        const services = helpers.offered();
        const out = await api(`${BASE}/items/${lead.id}/call-script`, { method: "POST", body: services.length ? { services } : {} });
        scripts.set(lead.id, out.script);
        fill(result, scriptView(out.script));
      }) });

      const outcome = ui.select(OUTCOME_FALLBACK, "no_answer", { id: "ldCallOutcome", "aria-label": "What happened" });
      const notes = h("textarea", { id: "ldCallNotes", rows: 2, maxlength: "2000", placeholder: "What they said, what to do next…", "aria-label": "Call notes" });
      const days = h("input", { id: "ldCallDays", type: "number", min: "0", max: "365", placeholder: "days", "aria-label": "Follow up in days" });
      const log = btn("Log this call", { icon: "check", id: "ldCallLog", onClick: (event) => busy(event.currentTarget, async () => {
        if (outcome.value === "do_not_call" && !(await Aira.confirm({ title: "Add to the do-not-contact list?", message: "This lead's phone number and email go on your do-not-contact list, and the lead is marked disqualified. Monosage will not draft or send anything to them again.", confirmLabel: "Add and log" }))) return;
        const out = await api(`${BASE}/items/${lead.id}/calls`, { method: "POST", body: { outcome: outcome.value, notes: notes.value, follow_up_days: days.value === "" ? null : Number(days.value) }, expect: 201 });
        toast("Call logged.", "success");
        document.dispatchEvent(new CustomEvent(CALL_LOGGED));
        helpers.update(out.lead);
      }) });

      fill(body,
        dial.number
          ? h("div", { class: "ld-dial" },
            h("p", {}, h("strong", { class: "ld-number", id: "ldCallNumber", text: dial.number }), dial.calling_from ? h("span", { class: "muted", text: ` · you call from ${dial.calling_from}` }) : null),
            blocked ? h("p", { class: "ld-warning", id: "ldCallBlocked", role: "alert", text: dial.blocked }) : null,
            h("div", { class: "btn-row" },
              blocked ? btn("Open in Google Voice", { icon: "phone", disabled: true, title: "On your do-not-contact list" }) : btn("Open in Google Voice", { icon: "phone", href: dial.google_voice_url, external: true, kind: "" }),
              blocked ? btn("Call with your phone", { icon: "phone", disabled: true, title: "On your do-not-contact list" }) : btn("Call with your phone", { icon: "phone", href: dial.tel_url })),
            h("p", { class: `ld-window ${windowTone}`.trim(), id: "ldCallWindow" }, tonedChip(windowLabel, windowTone), " ", when.note || ""))
          : h("p", { class: "muted", text: "This lead has no phone number. Read its website, or add one in the details above." }),
        h("ul", { class: "ld-reminders", "aria-label": "Before you call" }, ...(dial.reminders || []).map((r) => h("li", { text: r }))),
        h("div", { class: "btn-row" }, makeScript), result,
        h("div", { class: "ld-logcall" }, h("h4", { text: "Log the call" }),
          h("div", { class: "ld-grid" }, h("div", { class: "field" }, h("label", { for: "ldCallOutcome", text: "What happened" }), outcome), h("div", { class: "field narrow" }, h("label", { for: "ldCallDays", text: "Follow up in (days)" }), days)),
          h("div", { class: "field" }, h("label", { for: "ldCallNotes", text: "Notes" }), notes), h("div", { class: "btn-row" }, log)),
        calls.length ? h("ul", { class: "ld-calls", "aria-label": "Call history" }, ...calls.map((c) => h("li", {}, h("strong", { text: c.label }), h("span", { class: "muted", text: ` · ${fmt.datetime(c.created_at)}${c.follow_up_at ? ` · follow up ${fmt.datetime(c.follow_up_at)}` : ""}` }), c.notes ? h("div", { text: c.notes }) : null))) : h("p", { class: "muted", text: "No calls logged yet." }));
    }
    load();
    return box;
  }

  function emailBlock(lead, helpers) {
    const box = h("section", { class: "ld-block ld-email", "aria-label": "Email", id: "ldEmail" }, h("h3", { text: "Email" }));
    const list = h("div", { class: "ld-lead-messages" });
    const write = btn("Write email with Ollama", { icon: "spark", kind: "", disabled: !lead.email, title: lead.email ? "" : "Add or find an email address first", onClick: (event) => busy(event.currentTarget, async () => {
      const out = await writeEmails([lead.id], { use_model: true });
      if (out.created) await loadMessages();
    }) });
    box.append(
      h("p", { class: "muted", text: lead.email ? `To ${lead.email}. The draft goes to your Outbox; nothing is sent until you approve it there.` : "No email address yet. Read the website to look for one, or type one in the details above." }),
      h("div", { class: "btn-row" }, write, btn("Open the Outbox", { kind: "ghost", href: link("outreach") })), list);
    async function loadMessages() {
      try {
        const out = await api(`${BASE}/outbox?lead_id=${lead.id}`, { silent: true });
        fill(list, out.messages.length ? h("ul", { class: "ld-calls", "aria-label": "Messages for this lead" }, ...out.messages.map((m) => h("li", {}, tonedChip(STATUS_LABEL[m.status] || m.status, STATUS_TONE[m.status]), " ", h("strong", { text: m.subject }), h("span", { class: "muted", text: ` · ${authorLabel(m.source)}` })))) : null);
      } catch { /* the list is only a convenience */ }
    }
    loadMessages();
    return box;
  }

  function drawerSections(lead, helpers) {
    return { needs: needsBlock(lead, helpers), audit: auditBlock(lead, helpers), call: callBlock(lead, helpers), email: emailBlock(lead, helpers) };
  }

  // ---- follow-ups due ---------------------------------------------------------------------------------------------------

  function followUps({ openById }) {
    const box = h("div", { class: "ld-followups", id: "ldFollowUps" });
    const load = async () => {
      let items = [];
      try { items = (await api(`${BASE}/follow-ups`, { silent: true })).follow_ups || []; } catch { return; }
      if (!items.length) { fill(box); return; }
      const list = h("ul", { class: "ld-calls", id: "ldFollowList", hidden: true, "aria-label": "Follow-ups due" }, ...items.map((item) => h("li", {},
        h("div", {}, h("strong", { text: item.lead_name }), item.lead_company && item.lead_company !== item.lead_name ? h("span", { class: "muted", text: ` · ${item.lead_company}` }) : null,
          h("div", { class: "muted", text: `${item.label} · follow up was due ${fmt.datetime(item.follow_up_at)}${item.lead_phone ? ` · ${item.lead_phone}` : ""}` })),
        btn("Open", { sm: true, aria: `Open ${item.lead_name}`, onClick: (event) => busy(event.currentTarget, () => openById(item.lead_id)) }))));
      const toggle = h("button", { type: "button", class: "btn secondary sm ld-followbtn", id: "ldFollowBadge", "aria-expanded": "false", "aria-controls": "ldFollowList" }, ico("phone"), h("span", { text: `Follow-ups due (${items.length})` }));
      toggle.addEventListener("click", () => { list.hidden = !list.hidden; toggle.setAttribute("aria-expanded", String(!list.hidden)); });
      fill(box, toggle, list);
    };
    // a call logged in the drawer changes who is due: look again (and stop listening once this tab is gone)
    const again = () => { if (!box.isConnected) document.removeEventListener(CALL_LOGGED, again); else load(); };
    document.addEventListener(CALL_LOGGED, again);
    load();
    return box;
  }

  // ===========================================================================
  // The Outreach tab
  // ===========================================================================

  async function render(content, ctx) {
    const catalog = await loadCatalog();
    const state = { status: "", messages: [], counts: {}, settings: {}, hasPassword: false, problems: [], suppressions: [], editing: null, sendResult: null, limits: { max_daily_cap: 100, min_delay: 2, batch: 10 } };
    const strip = h("div", { class: "ld-strip", role: "status", "aria-live": "polite" });
    const outboxBox = h("section", { class: "panel ld-panel", id: "ldOutbox", "aria-labelledby": "ldOutboxTitle" });
    const senderBox = h("section", { class: "panel ld-panel", id: "ldSender", "aria-labelledby": "ldSenderTitle" });
    const dncBox = h("section", { class: "panel ld-panel", id: "ldDnc", "aria-labelledby": "ldDncTitle" });

    async function loadAll() {
      content.replaceChildren(ui.skeleton(5));
      try {
        const [settings, outbox, dnc] = await Promise.all([api(`${BASE}/outreach/settings`), api(`${BASE}/outbox${state.status ? `?status=${state.status}` : ""}`), api(`${BASE}/suppressions`)]);
        Object.assign(state, { settings: settings.settings, hasPassword: settings.has_password, problems: settings.problems, limits: settings.limits || state.limits, counts: outbox.counts, messages: outbox.messages, suppressions: dnc.suppressions });
      } catch (error) { content.replaceChildren(K.problem(error, () => K.router.render())); return false; }
      return true;
    }
    async function refreshOutbox() {
      const out = await api(`${BASE}/outbox${state.status ? `?status=${state.status}` : ""}`, { silent: true });
      state.messages = out.messages; state.counts = out.counts;
      paintStrip(); paintOutbox();
    }

    // ---- the strip of numbers ----
    function paintStrip() {
      const s = outboxState(state.counts, state.settings);
      const stat = (label, value, id) => h("div", { class: "ld-stat", id }, h("strong", { text: String(value) }), h("span", { text: label }));
      fill(strip, stat("Drafts to review", s.drafts, "ldStatDrafts"), stat("Approved, waiting", s.approved, "ldStatApproved"), stat(`Sent today (cap ${s.cap})`, s.sentToday, "ldStatSent"), stat("Left today", s.remaining, "ldStatLeft"),
        state.problems.length ? h("div", { class: "ld-stat warn", id: "ldStatSetup" }, h("strong", { text: "Setup" }), h("span", { text: `${state.problems.length} thing${state.problems.length === 1 ? "" : "s"} missing` })) : h("div", { class: "ld-stat ok", id: "ldStatSetup" }, h("strong", { text: "Ready" }), h("span", { text: "sender settings complete" })));
    }

    // ---- the outbox ----
    function messageCard(m) {
      const editing = state.editing === m.id;
      const isOpen = ["draft", "approved", "failed", "skipped"].includes(m.status);
      const head = h("div", { class: "ld-msg-head" },
        h("div", {}, h("strong", { text: m.lead_name || "A lead" }), m.lead_company && m.lead_company !== m.lead_name ? h("span", { class: "muted", text: ` · ${m.lead_company}` }) : null, h("div", { class: "muted", text: `To ${m.to_address}` })),
        h("div", { class: "ld-msg-chips" }, tonedChip(STATUS_LABEL[m.status] || m.status, STATUS_TONE[m.status]), tonedChip(authorLabel(m.source), m.source === "model" ? "ok" : "")));
      let bodyNode;
      let actions;
      if (editing) {
        const subject = h("input", { id: `ldMsgSubject-${m.id}`, type: "text", maxlength: "200", value: m.subject, "aria-label": "Subject" });
        const text = h("textarea", { id: `ldMsgBody-${m.id}`, rows: 9, maxlength: "4000", "aria-label": "Message" });
        text.value = m.body;
        bodyNode = h("div", { class: "ld-msg-edit" }, h("div", { class: "field" }, h("label", { for: subject.id, text: "Subject" }), subject), h("div", { class: "field" }, h("label", { for: text.id, text: "Message" }), text), h("p", { class: "muted", text: "Saving withdraws any approval: approve it again after editing." }));
        actions = [
          btn("Save", { icon: "save", kind: "", onClick: (event) => busy(event.currentTarget, async () => { await api(`${BASE}/outbox/${m.id}`, { method: "PATCH", body: { subject: subject.value, body: text.value } }); state.editing = null; toast("Draft saved.", "success"); await refreshOutbox(); }) }),
          btn("Cancel", { kind: "ghost", onClick: () => { state.editing = null; paintOutbox(); } }),
        ];
      } else {
        bodyNode = h("div", { class: "ld-msg-text" }, h("p", { class: "ld-msg-subject" }, h("span", { class: "muted", text: "Subject: " }), m.subject), h("pre", { class: "ld-msg-body", text: m.body }),
          m.error ? h("p", { class: "ld-warning", role: "note", text: m.error }) : null,
          m.status === "sent" ? h("p", { class: "muted", text: `Sent ${fmt.datetime(m.sent_at)}.` }) : null);
        const act = (label, fn, opts = {}) => btn(label, { sm: true, ...opts, onClick: (event) => busy(event.currentTarget, async () => { await fn(); await refreshOutbox(); }) });
        actions = [
          ["draft", "failed", "skipped"].includes(m.status) ? act("Approve", async () => { await api(`${BASE}/outbox/approve`, { method: "POST", body: { ids: [m.id] } }); toast("Approved. It goes out when you press Send.", "success"); }, { kind: "", icon: "check" }) : null,
          isOpen ? btn("Edit", { sm: true, icon: "edit", onClick: () => { state.editing = m.id; paintOutbox(); } }) : null,
          isOpen ? act("Reject", async () => { await api(`${BASE}/outbox/reject`, { method: "POST", body: { ids: [m.id] } }); }, { kind: "ghost", icon: "x" }) : null,
          m.status !== "sent" ? act("Delete", async () => { await api(`${BASE}/outbox/${m.id}`, { method: "DELETE" }); }, { kind: "ghost", icon: "trash", aria: `Delete the message to ${m.to_address}` }) : null,
          m.status === "sent" ? act("Do not contact", async () => { await api(`${BASE}/suppressions`, { method: "POST", body: { value: m.to_address, reason: "Asked to stop (from the outbox)" }, expect: 201 }); toast(`${m.to_address} is on your do-not-contact list.`, "success"); await refreshDnc(); }, { kind: "ghost", icon: "shield" }) : null,
        ];
      }
      return h("article", { class: `ld-msg ${m.status}`, dataset: { id: m.id }, "aria-label": `Message to ${m.to_address}` }, head, bodyNode, h("div", { class: "btn-row ld-msg-actions" }, ...actions));
    }

    function sendResultNode() {
      const r = state.sendResult;
      if (!r) return null;
      if (r.error) return h("div", { class: "ld-notice warn", id: "ldSendResult", role: "alert" }, h("span", { class: "ld-notice-ico" }, ico("warn")), h("div", {}, h("strong", { text: "Nothing was sent" }), h("p", { text: r.error })));
      const nameOf = (id) => (r.names && r.names[id]) || "A message";
      return h("div", { class: "ld-notice", id: "ldSendResult", role: "status" }, h("span", { class: "ld-notice-ico" }, ico("send")), h("div", {},
        h("strong", { text: `Sent ${r.sent}${r.failed ? `, ${r.failed} failed` : ""}${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}.` }),
        r.note ? h("p", { text: r.note }) : null,
        r.details.length ? h("ul", { class: "ld-skipped" }, ...r.details.map((d) => h("li", { text: d.status === "sent" ? `Sent to ${d.to}` : `Failed: ${d.error}` }))) : null,
        r.skipped.length ? h("ul", { class: "ld-skipped", "aria-label": "Skipped" }, ...r.skipped.map((s) => h("li", {}, h("strong", { text: nameOf(s.id) }), `: ${s.reason}`))) : null,
        h("p", { class: "muted", text: `${r.remaining_today} left today of a cap of ${r.daily_cap}.` })));
    }

    function paintOutbox() {
      const s = outboxState(state.counts, state.settings);
      const filter = ui.select(OUTBOX_STATUSES, state.status, { id: "ldOutboxStatus", "aria-label": "Show messages" });
      filter.addEventListener("change", async () => { state.status = filter.value; await refreshOutbox(); });
      const drafts = state.messages.filter((m) => m.status === "draft");
      const approveAll = btn(`Approve all drafts (${drafts.length})`, { icon: "check", disabled: !drafts.length, onClick: (event) => busy(event.currentTarget, async () => { const out = await api(`${BASE}/outbox/approve`, { method: "POST", body: { ids: drafts.map((m) => m.id) } }); toast(`${plural(out.approved, "message")} approved.`, "success"); await refreshOutbox(); }) });
      const send = btn(s.willSend ? `Send approved (${s.willSend})` : "Send approved", { icon: "send", kind: "", disabled: !s.canSend, onClick: (event) => busy(event.currentTarget, async () => {
        const names = Object.fromEntries(state.messages.map((m) => [m.id, m.lead_name || m.to_address]));
        try { state.sendResult = { ...(await api(`${BASE}/outbox/send`, { method: "POST", body: {}, silent: true })), names }; }
        catch (error) { state.sendResult = { error: message(error) }; }
        await refreshOutbox();
      }) });
      fill(outboxBox,
        h("div", { class: "panel-head" }, h("h2", { id: "ldOutboxTitle", text: "Outbox" }), h("div", { class: "btn-row" }, h("label", { class: "ld-pick" }, h("span", { text: "Show" }), filter), approveAll, send)),
        h("div", { class: "panel-body" },
          h("p", { class: "ld-notice-line", id: "ldOutboxRule" }, ico("shield"), h("span", {}, " Nothing is sent until you approve it. Each approved message goes out through your own mail server, one at a time with a pause, up to your daily cap, with your name, your postal address and an opt-out line, and only to addresses that are not on your do-not-contact list.")),
          h("p", { class: "muted", id: "ldSendState", text: s.approved ? `${plural(s.approved, "message")} approved and waiting. ${s.remaining} left today (cap ${s.cap}); each press sends at most 10.` : `No message is approved. Sent today: ${s.sentToday} of ${s.cap}.` }),
          sendResultNode(),
          state.messages.length ? h("div", { class: "ld-msgs", id: "ldMessages" }, ...state.messages.map(messageCard)) : ui.empty(state.status ? "No messages with that status" : "The outbox is empty", state.status ? "Choose another status." : "Select leads on the Leads tab and press “Write emails”, or open a lead and press “Write email with Ollama”.", btn("Open Leads", { kind: "", href: link("leads") }))));
    }

    // ---- the sender settings ----
    function paintSender(note = null) {
      const st = state.settings;
      const f = {};
      const field = (def, value) => { const node = textField(def, value, { id: `ldset-${def.name}` }); f[def.name] = node.querySelector("input, textarea"); return node; };
      const security = ui.select([["starttls", "STARTTLS (port 587)"], ["ssl", "SSL/TLS (port 465)"], ["none", "None (a local test server only)"]], st.smtp_security || "starttls", { id: "ldset-smtp_security", "aria-label": "Connection security" });
      const password = h("input", { id: "ldset-smtp_password", type: "password", autocomplete: "new-password", placeholder: state.hasPassword ? "Saved: leave empty to keep it" : "Mail server password", "aria-label": "Mail server password" });
      const clear = h("input", { type: "checkbox", id: "ldset-clear_password" });
      const serviceBoxes = catalog.map((service) => h("label", { class: "check" }, h("input", { type: "checkbox", id: `ldset-service-${service.id}`, value: service.id, checked: (st.services || []).includes(service.id) || null }), h("span", { text: service.label })));
      const lawful = h("input", { type: "checkbox", id: "ldset-allow_regulated_regions", checked: st.allow_regulated_regions || null });
      const result = h("p", { class: `ld-formerror${note && note.ok ? " ok" : ""}`, id: "ldSettingsResult", role: "status", "aria-live": "polite", text: note ? note.text : "" });
      const missing = h("p", { class: state.problems.length ? "ld-warning" : "muted", id: "ldProblems", text: state.problems.length ? problemsText(state.problems) : "Sender settings are complete." });
      const collect = () => ({
        ...Object.fromEntries(Object.entries(f).map(([k, node]) => [k, node.value])),
        smtp_security: security.value, smtp_password: password.value, clear_password: clear.checked,
        services: serviceBoxes.map((b) => b.querySelector("input")).filter((i) => i.checked).map((i) => i.value), allow_regulated_regions: lawful.checked,
      });
      const save = async () => {
        const out = await api(`${BASE}/outreach/settings`, { method: "POST", body: settingsPayload(collect()) });
        Object.assign(state, { settings: out.settings, hasPassword: out.has_password, problems: out.problems });
        password.value = ""; clear.checked = false;
        return out;
      };
      const saveBtn = btn("Save sender settings", { icon: "save", kind: "", id: "ldSettingsSave", onClick: (event) => busy(event.currentTarget, async () => { await save(); toast("Sender settings saved.", "success"); paintSender({ text: "Saved.", ok: true }); paintStrip(); paintOutbox(); }) });
      const testBtn = btn("Send a test email", { icon: "send", id: "ldSettingsTest", onClick: (event) => busy(event.currentTarget, async () => {
        let saved;
        try { saved = await save(); }
        catch (error) { result.className = "ld-formerror"; result.textContent = message(error); return; } // keep what was typed
        let outcome;
        if (saved.problems.length) outcome = { text: problemsText(saved.problems), ok: false };
        else {
          try {
            const out = await api(`${BASE}/outreach/test`, { method: "POST", body: {}, silent: true });
            outcome = { text: `Test email sent to ${out.sent_to}. Check that inbox.`, ok: true };
          } catch (error) { outcome = { text: message(error), ok: false }; }
        }
        paintSender(outcome); paintStrip(); paintOutbox();
      }) });
      fill(senderBox,
        h("div", { class: "panel-head" }, h("h2", { id: "ldSenderTitle", text: "Sender settings" }), h("small", { class: "muted", text: "Your own mail server. Monosage has no shared mailer." })),
        h("div", { class: "panel-body" }, h("form", { class: "ld-form", id: "ldSenderForm", novalidate: "true", "aria-label": "Sender settings", onsubmit: (event) => event.preventDefault() },
          missing,
          h("div", { class: "ld-grid" },
            field({ name: "from_name", label: "Your name", placeholder: "Sam Seller" }, st.from_name), field({ name: "from_email", label: "From address", type: "email", placeholder: "sam@yourdomain.com" }, st.from_email), field({ name: "reply_to", label: "Reply-to (optional)", type: "email" }, st.reply_to),
            field({ name: "smtp_host", label: "Mail server", placeholder: "smtp.yourprovider.com" }, st.smtp_host), field({ name: "smtp_port", label: "Port", type: "number", maxlength: "5" }, String(st.smtp_port || 587)),
            h("div", { class: "field" }, h("label", { for: "ldset-smtp_security", text: "Connection security" }), security),
            field({ name: "smtp_username", label: "Username (if the server asks)" }, st.smtp_username),
            h("div", { class: "field" }, h("label", { for: "ldset-smtp_password", text: "Password" }), password, h("span", { class: "hint", text: state.hasPassword ? "A password is saved (encrypted). Type a new one to replace it." : "Stored encrypted. Never shown again." }), state.hasPassword ? h("label", { class: "check" }, clear, h("span", { text: "Remove the saved password" })) : null),
            field({ name: "postal_address", label: "Postal address for the footer", multiline: true, rows: 2, wide: true, hint: "Commercial email rules (CAN-SPAM and others) require a real postal address in every message." }, st.postal_address),
            field({ name: "signature", label: "Signature (optional)", placeholder: "Sam Seller, Seller Web Studio" }, st.signature),
            field({ name: "calling_number", label: "Your calling number (display only)", placeholder: "Your Google Voice number", hint: "Shown in call notes. Monosage never dials from it." }, st.calling_number),
            field({ name: "offer", label: "What you offer, in a sentence", multiline: true, rows: 2, wide: true, hint: "Used in email drafts and talk tracks. Monosage adds nothing about you that is not here." }, st.offer),
            field({ name: "daily_cap", label: `Emails per day (1 to ${state.limits.max_daily_cap})`, type: "number", maxlength: "3" }, String(st.daily_cap || 20)), field({ name: "delay_seconds", label: `Pause between emails, seconds (${state.limits.min_delay} or more)`, type: "number", maxlength: "3" }, String(st.delay_seconds || 5))),
          h("fieldset", { class: "ld-fieldset", id: "ldsetServices" }, h("legend", { text: "Services you offer" }), h("div", { class: "ld-checks" }, ...serviceBoxes)),
          h("fieldset", { class: "ld-fieldset", id: "ldsetLawful" }, h("legend", { text: "People in the EU, UK or Canada" }),
            h("label", { class: "check" }, lawful, h("span", { text: "I have a lawful basis to email people in the EU, UK or Canada" })),
            h("p", { class: "hint", text: "By default Monosage refuses to draft or send cold email to leads whose location looks like the EU, the UK or Canada, where consent rules are strict (GDPR, PECR, CASL). Tick this only if you have consent or another lawful basis; that is your responsibility, not Monosage's." })),
          h("div", { class: "btn-row" }, saveBtn, testBtn), result)));
    }

    // ---- the do-not-contact list ----
    async function refreshDnc() {
      state.suppressions = (await api(`${BASE}/suppressions`, { silent: true })).suppressions;
      paintDnc();
    }
    function paintDnc() {
      const value = h("input", { id: "ldDncValue", type: "text", maxlength: "200", placeholder: "name@company.com, company.com or +1 512 555 0100", "aria-label": "Email, domain or phone number" });
      const reason = h("input", { id: "ldDncReason", type: "text", maxlength: "200", placeholder: "Why (optional)", "aria-label": "Reason" });
      const error = h("p", { class: "ld-formerror", id: "ldDncError", role: "alert" });
      const add = btn("Add to the list", { icon: "plus", id: "ldDncAdd", kind: "", onClick: (event) => busy(event.currentTarget, async () => {
        error.textContent = "";
        try { await api(`${BASE}/suppressions`, { method: "POST", body: { value: value.value, reason: reason.value }, expect: 201, silent: true }); }
        catch (err) { error.textContent = message(err); return; }
        toast("Added. Monosage will not draft or send anything to them.", "success");
        await refreshDnc();
      }) });
      fill(dncBox,
        h("div", { class: "panel-head" }, h("h2", { id: "ldDncTitle", text: "Do-not-contact list" }), h("small", { class: "muted", text: `${state.suppressions.length} ${state.suppressions.length === 1 ? "entry" : "entries"}` })),
        h("div", { class: "panel-body" },
          h("p", { class: "muted", text: "Anyone here is skipped when drafting and again when sending, and their number is flagged in the call panel. Add an email, a whole domain or a phone number. People who reply “unsubscribe” or ask not to be called belong here." }),
          h("div", { class: "ld-grid" }, h("div", { class: "field" }, h("label", { for: "ldDncValue", text: "Email, domain or phone" }), value), h("div", { class: "field" }, h("label", { for: "ldDncReason", text: "Reason" }), reason)),
          error, h("div", { class: "btn-row" }, add),
          state.suppressions.length ? h("div", { class: "table-wrap ld-table-wrap" }, h("table", { class: "table ld-table", id: "ldDncTable" },
            h("caption", { class: "visually-hidden", text: "Do-not-contact list" }),
            h("thead", {}, h("tr", {}, ...["Entry", "Kind", "Reason", "Added", ""].map((t) => h("th", { text: t })))),
            h("tbody", {}, ...state.suppressions.map((s) => h("tr", { dataset: { key: s.key } },
              h("td", { "data-label": "Entry", text: s.value }), h("td", { "data-label": "Kind", text: s.kind }), h("td", { "data-label": "Reason", text: s.reason || "—" }), h("td", { "data-label": "Added", text: fmt.datetime(s.created_at) }),
              h("td", {}, btn("Remove", { sm: true, kind: "ghost", aria: `Remove ${s.value}`, onClick: (event) => busy(event.currentTarget, async () => { await api(`${BASE}/suppressions/remove`, { method: "POST", body: { key: s.key } }); toast("Removed from the list.", "success"); await refreshDnc(); }) }))))))) : null));
    }

    const compliance = h("section", { class: "panel ld-panel ld-compliance", id: "ldCompliance", "aria-labelledby": "ldComplianceTitle" },
      h("div", { class: "panel-head" }, h("h2", { id: "ldComplianceTitle", text: "The rules, in plain words" }), h("small", { class: "muted", text: "Not legal advice" })),
      h("div", { class: "panel-body" }, h("ul", { class: "ld-rules" },
        h("li", {}, h("strong", { text: "Email. " }), "In the US (CAN-SPAM) a commercial email needs your real name and postal address, an honest subject and a working way to opt out, and you must honour opt-outs. In the EU and UK (GDPR, PECR) and Canada (CASL), emailing people you have no relationship with generally needs their consent; business-to-business rules differ by country. Monosage adds the footer and checks your do-not-contact list, and blocks those regions unless you confirm a lawful basis."),
        h("li", {}, h("strong", { text: "Calls. " }), "You place every call yourself. In the US (TCPA) an artificial or prerecorded voice, including an AI voice, needs the called person's prior express written consent for marketing, and calls to numbers on the Do Not Call registry and outside 8 am to 9 pm local time are restricted. Other countries have their own registries and registration rules. Google Voice's policy also forbids automated calling and bulk messages."),
        h("li", {}, h("strong", { text: "Public posts. " }), "A person who posted a request did so in a public place. Reply there, in your own name, within that community's rules. Do not copy their details into a mailing list."))));

    if (!(await loadAll())) return;
    paintStrip(); paintOutbox(); paintSender(); paintDnc();
    fill(content, h("div", { class: "ld-outreach" },
      h("nav", { class: "ld-jump", "aria-label": "On this page" }, h("a", { href: "#ldOutbox", text: "Outbox" }), h("a", { href: "#ldSender", text: "Sender settings" }), h("a", { href: "#ldDnc", text: "Do-not-contact list" })),
      strip, outboxBox, senderBox, dncBox, compliance));
  }

  Aira.leads.outreach = { render, writeEmails, showDraftResult };
  Aira.leads.extras = { drawerSections, followUps };
})();

/* Monosage — recruiter contacts: Find recruiters, Lists (email and LinkedIn drafts), and each contact's story.
   Backend: aira/recruiter_contacts.py and aira/recruiter_outreach.py. */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, withBusy, router } = Aira;
  // Element.replaceChildren prints a null as the text "null": drop the empty slots.
  const put = (node, ...children) => node.replaceChildren(...children.filter((child) => child !== null && child !== undefined && child !== false));
  const SOURCE_LABELS = { linkedin_inbox: "LinkedIn inbox", email: "Email", interview: "Interview", manual: "Added by you" };
  const sourceBadges = (kinds) => (kinds || []).map((kind) => h("span", { class: "badge", text: SOURCE_LABELS[kind] || kind }));
  const FIELD_LABELS = { title: "title", company: "company", email: "email" };
  const WHERE = { "LinkedIn headline": "their LinkedIn headline", "Their message": "their message", Email: "the email", "Email sender": "the email sender", "Email signature": "the email signature", Interview: "the interview" };
  const LINK_LABELS = { linkedin_inbox: "Open conversation", interview: "Open interview", email: "Email updates" };

  // -------------------------------------------------------------------------
  // Find recruiters: proposals with checkboxes, then Save selected
  // -------------------------------------------------------------------------
  Aira.renderRecruiterFind = async (host) => {
    const results = h("div", { class: "panel" });
    const again = h("button", { type: "button", class: "btn secondary sm", text: "Look again" });
    put(host, 
      h("div", { class: "card rcx-intro" },
        h("h3", { text: "Find recruiters" }),
        h("p", { class: "muted", text: "Monosage reads your saved LinkedIn conversations, imported emails and interview participants, and proposes new contacts or updates to the ones you have. Every detail comes from that text. Nothing is looked up or guessed, so a person without an email address in the text stays without one." }),
        h("div", { class: "btn-row" }, again)),
      results,
    );
    const load = async () => {
      put(results, ui.skeleton(4));
      let data;
      try { data = await api("/api/recruiters/find"); } catch (error) { put(results, ui.error(error, load)); return; }
      const scanned = data.scanned || {};
      const summary = h("p", { class: "muted rcx-scanned", text: `Checked ${fmt.plural(scanned.linkedin_inbox || 0, "conversation")}, ${fmt.plural(scanned.email || 0, "email")} and ${fmt.plural(scanned.interview || 0, "interview participant")}. ${data.up_to_date ? `${fmt.plural(data.up_to_date, "contact")} already up to date.` : ""}`.trim() });
      if (!data.proposals.length) {
        put(results, summary, ui.empty("Nothing new to add", "Every person found is already a contact with the same details. Sync your LinkedIn inbox or import recruiter emails, then look again."));
        return;
      }
      const boxes = [];
      const saveButton = h("button", { type: "button", class: "btn sm", id: "rcxSave" });
      const count = () => { const n = boxes.filter((box) => box.checked).length; saveButton.textContent = `Save selected (${n})`; saveButton.disabled = n === 0; };
      const rows = h("div", { class: "list rcx-proposals" });
      for (const item of data.proposals) {
        const box = h("input", { type: "checkbox", checked: item.selected, "aria-label": `Save ${item.name}`, dataset: { key: item.key } });
        box.addEventListener("change", count);
        boxes.push(box);
        const facts = [item.title, item.company, item.email].filter(Boolean).join(" · ");
        const notes = [];
        if (item.match && item.match.how === "name") notes.push(h("p", { class: "note warn", text: `Same name as your contact ${item.match.name}${item.match.company ? ` (${item.match.company})` : ""}. Tick it only if this is the same person.` }));
        if (!item.looks_like_recruiter) notes.push(h("p", { class: "muted small", text: "Not clearly a recruiter: their title has no recruiting words." }));
        if (!item.email) notes.push(h("p", { class: "muted small", text: "No email address in the text. This contact stays LinkedIn only." }));
        const changes = Object.keys(item.changes || {}).filter((key) => key !== "name").map((key) => FIELD_LABELS[key] || key);
        // "Title and company from their LinkedIn headline · email from the email sender"
        const byWhere = {};
        for (const [key, where] of Object.entries(item.evidence || {})) if (key !== "name") (byWhere[where] = byWhere[where] || []).push(FIELD_LABELS[key] || key);
        const joined = (fields) => (fields.length > 1 ? `${fields.slice(0, -1).join(", ")} and ${fields[fields.length - 1]}` : fields[0]);
        const evidence = Object.entries(byWhere).map(([where, fields]) => `${joined(fields)} from ${WHERE[where] || where}`);
        if (evidence.length) evidence[0] = evidence[0][0].toUpperCase() + evidence[0].slice(1);
        rows.append(h("div", { class: "row rcx-proposal" },
          h("label", { class: "check rcx-pick" }, box),
          h("div", { class: "rcx-body" },
            h("span", { class: "title", text: item.name }),
            facts ? h("span", { class: "sub", text: facts }) : null,
            h("div", { class: "meta" },
              item.action === "new" ? h("span", { class: "badge stage applied", text: "New contact" }) : h("span", { class: "badge stage saved", text: `Update ${item.match.name}` }),
              ...sourceBadges(item.source_kinds),
              item.last_contact_at ? h("span", { class: "badge", text: `Last contact ${fmt.date(item.last_contact_at)}` }) : null),
            item.action === "update" && changes.length ? h("p", { class: "small", text: `Adds: ${changes.join(", ")}` }) : null,
            item.action === "update" && !changes.length ? h("p", { class: "small muted", text: "Adds where you talked; the details stay as they are." }) : null,
            evidence.length ? h("p", { class: "small muted rcx-evidence", text: evidence.join(" · ") }) : null,
            ...notes,
            h("div", { class: "rcx-links" }, ...item.sources.filter((source) => source.link).map((source) => h("a", { href: source.link, class: "small", text: `${LINK_LABELS[source.kind] || source.label}${source.kind === "email" ? `: ${source.label}` : ""}` }))))));
      }
      saveButton.addEventListener("click", () => withBusy(saveButton, async () => {
        const keys = boxes.filter((box) => box.checked).map((box) => box.dataset.key);
        const result = await api("/api/recruiters/find/save", { method: "POST", body: { keys } });
        const parts = [`Saved ${fmt.plural(result.created, "new contact")}`];
        if (result.updated) parts.push(`updated ${fmt.plural(result.updated, "contact")}`);
        if (result.stale) parts.push(`${result.stale} changed since you looked, so they were not saved`);
        toast(`${parts.join(", ")}.`, result.failed.length ? "error" : "success");
        for (const failure of result.failed) toast(`${failure.name}: ${failure.error}`, "error");
        load();
      }));
      const all = h("button", { type: "button", class: "btn ghost sm", text: "Select all", onclick: () => { boxes.forEach((box) => { box.checked = true; }); count(); } });
      const none = h("button", { type: "button", class: "btn ghost sm", text: "Select none", onclick: () => { boxes.forEach((box) => { box.checked = false; }); count(); } });
      put(results, 
        h("div", { class: "panel-head" }, h("h3", { text: "People found" }), h("small", { class: "muted", text: String(data.proposals.length) })),
        summary,
        h("div", { class: "btn-row rcx-toolbar" }, all, none, saveButton),
        rows);
      count();
    };
    again.addEventListener("click", load);
    await load();
  };

  // -------------------------------------------------------------------------
  // Lists: saved filters over contacts, CSV, email and LinkedIn drafts
  // -------------------------------------------------------------------------
  const filterText = (filters) => {
    const parts = [];
    if (filters.applied_company) parts.push("at companies you applied to");
    if (filters.no_reply_days) parts.push(`no reply in ${filters.no_reply_days} days`);
    if (filters.has_email) parts.push("has email");
    if (filters.source) parts.push(`source: ${SOURCE_LABELS[filters.source] || filters.source}`);
    if (filters.q) parts.push(`matches “${filters.q}”`);
    return parts.length ? parts.join(" · ") : "Every contact";
  };

  Aira.renderRecruiterLists = async (host, route) => {
    const listId = /^[0-9a-f]{32}$/.test(route.query.list || "") ? route.query.list : "";
    const side = h("div", { class: "panel" });
    const main = h("div", {});
    put(host, h("div", { class: "grid sidebar-right rcx-lists" }, main, side));
    let data;
    const reload = () => router.render();
    try { data = await api("/api/recruiters/lists"); } catch (error) { put(host, ui.error(error, reload)); return; }
    put(side, 
      h("div", { class: "panel-head" }, h("h3", { text: "Start from" })),
      h("div", { class: "panel-body" },
      h("p", { class: "muted small", style: { marginTop: "0" }, text: "A list is a saved filter. It updates by itself as your contacts change." }),
      h("div", { class: "rcx-presets" }, ...data.presets.map((preset) => h("button", { type: "button", class: "btn secondary sm", text: preset.name, onclick: () => listForm(null, preset, (saved) => router.go(["recruiters"], { tab: "lists", list: saved.list_id })) }))),
      h("div", { class: "btn-row", style: { marginTop: "10px" } }, h("button", { type: "button", class: "btn sm", text: "+ New list", onclick: () => listForm(null, { name: "", filters: {} }, (saved) => router.go(["recruiters"], { tab: "lists", list: saved.list_id })) }))));
    const rows = h("div", { class: "list", "data-roving": "1" });
    if (!data.lists.length) rows.append(ui.empty("No lists yet", "Start from one of the lists on the right, or make your own."));
    for (const item of data.lists) rows.append(h("a", { class: `row${item.list_id === listId ? " active" : ""}`, href: `#/recruiters?tab=lists&list=${item.list_id}`, "data-roving-item": "1", "aria-current": item.list_id === listId ? "true" : "false" }, h("div", {}, h("span", { class: "title", text: item.name }), h("span", { class: "sub", text: filterText(item.filters) })), h("small", { class: "muted", text: fmt.plural(item.count, "contact") })));
    put(main, h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Your lists" }), h("small", { class: "muted", text: String(data.lists.length) })), rows));
    const chosen = data.lists.find((item) => item.list_id === listId);
    if (chosen) main.append(await listDetail(chosen));
  };

  async function listDetail(list) {
    const box = h("div", { class: "panel rcx-detail", style: { marginTop: "14px" } }, ui.skeleton(3));
    let data;
    try { data = await api(`/api/recruiters/lists/${list.list_id}/members`); } catch (error) { put(box, ui.error(error)); return box; }
    const people = data.members;
    const table = people.length ? h("table", { class: "table rcx-members" },
      h("thead", {}, h("tr", {}, h("th", { text: "Name" }), h("th", { text: "Company" }), h("th", { text: "Email" }), h("th", { text: "Found in" }), h("th", { text: "Last contact" }))),
      h("tbody", {}, ...people.map((p) => h("tr", {},
        h("td", {}, h("a", { href: `#/recruiters/${p.recruiter_id}`, text: p.name })),
        h("td", { text: p.company || "—" }),
        h("td", {}, p.email ? p.email : h("span", { class: "muted", text: "LinkedIn only" })),
        h("td", {}, ...sourceBadges(p.sources)),
        h("td", { text: p.last_contact_at ? fmt.date(p.last_contact_at) : "—" }))))) : ui.empty("Nobody matches this list yet", "Change the filters, or add contacts from Find recruiters.");
    const withEmail = people.filter((p) => p.email).length;
    put(box, 
      h("div", { class: "panel-head" }, h("h3", { text: list.name }), h("small", { class: "muted", text: fmt.plural(people.length, "contact") })),
      h("p", { class: "muted small", text: `${filterText(list.filters)}. ${people.length - withEmail ? `${people.length - withEmail} of them have no email address and are LinkedIn only.` : ""}`.trim() }),
      h("div", { class: "btn-row rcx-toolbar" },
        // Recruiter email uses the single-user build's mail server; the hosted build has no recruiter email.
        Aira.cloud ? null : h("button", { type: "button", class: "btn sm", text: "Email this list", disabled: !people.length, onclick: () => emailFlow(list) }),
        h("button", { type: "button", class: "btn secondary sm", text: "LinkedIn drafts", disabled: !people.length, onclick: () => linkedinFlow(list) }),
        h("button", { type: "button", class: "btn secondary sm", text: "Export CSV", onclick: () => Aira.download(`/api/recruiters/lists/${list.list_id}/export.csv`, `recruiters-${list.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.csv`) }),
        h("button", { type: "button", class: "btn ghost sm", text: "Edit list", onclick: () => listForm(list, list, () => router.render()) }),
        h("button", { type: "button", class: "btn ghost sm danger", text: "Delete list", onclick: async (event) => { if (await Aira.confirm({ title: "Delete this list?", message: "The contacts stay; only the saved filter is removed.", confirmLabel: "Delete list", danger: true })) await withBusy(event.currentTarget, async () => { await api(`/api/recruiters/lists/${list.list_id}`, { method: "DELETE" }); toast("List deleted.", "success"); router.go(["recruiters"], { tab: "lists" }); }); } })),
      table);
    return box;
  }

  function listForm(existing, start, onSaved) {
    const f = start.filters || {};
    const name = h("input", { type: "text", value: start.name || "", maxlength: 80, required: true });
    const applied = h("input", { type: "checkbox", checked: Boolean(f.applied_company) });
    const hasEmail = h("input", { type: "checkbox", checked: Boolean(f.has_email) });
    const days = h("input", { type: "number", min: 1, max: 365, value: f.no_reply_days || "", placeholder: "Any" });
    const source = ui.select([["", "Any source"], ["linkedin_inbox", "LinkedIn inbox"], ["email", "Email"], ["interview", "Interview"], ["manual", "Added by you"]], f.source || "");
    const q = h("input", { type: "search", value: f.q || "", maxlength: 100, placeholder: "Name, company, title or email" });
    const count = h("p", { class: "muted small", role: "status", "aria-live": "polite" });
    const filters = () => ({ applied_company: applied.checked, has_email: hasEmail.checked, no_reply_days: days.value ? Number(days.value) : null, source: source.value, q: q.value });
    let timer;
    const preview = () => { clearTimeout(timer); timer = setTimeout(async () => { try { const result = await api("/api/recruiters/lists/preview", { method: "POST", body: { filters: filters() }, silent: true }); count.textContent = `${fmt.plural(result.count, "contact")} match right now.`; } catch (error) { count.textContent = error.message; } }, 250); };
    for (const input of [applied, hasEmail, days, source, q]) { input.addEventListener("input", preview); input.addEventListener("change", preview); }
    const body = h("div", {},
      h("div", { class: "form-grid" }, ui.field("List name", name), ui.field("Found in", source), ui.field("No reply in (days)", days, "You wrote last, and they have not answered for at least this many days."), ui.field("Search", q)),
      h("label", { class: "check", style: { marginTop: "10px" } }, applied, "At companies I applied to"),
      h("label", { class: "check" }, hasEmail, "Has an email address"),
      count);
    preview();
    Aira.modal({ title: existing ? "Edit list" : "New list", body, actions: [{ label: "Cancel", kind: "ghost" }, { label: existing ? "Save list" : "Create list", kind: "", onClick: async () => {
      try {
        const payload = { name: name.value, filters: filters() };
        const result = existing ? await api(`/api/recruiters/lists/${existing.list_id}`, { method: "PATCH", body: payload }) : await api("/api/recruiters/lists", { method: "POST", body: payload });
        toast(existing ? "List saved." : "List created.", "success");
        onSaved(result.list);
      } catch (error) { Aira.reportError(error); return false; }
    } }] });
  }

  const STATUS = { ready: ["Ready", "stage applied"], skipped: ["Skipped", "stage rejected"], later: ["Tomorrow", "stage saved"] };
  const placeholderHint = (names) => `Details you can use: ${names.map((n) => `{${n}}`).join(", ")}. Give one a fallback after a bar, like {company|your team}; a contact missing a detail without a fallback is skipped.`;

  async function emailFlow(list) {
    let dialog;
    const closeDialog = () => { if (dialog) dialog.close(); };
    let view;
    try { view = await api("/api/recruiters/outreach"); } catch (error) { Aira.reportError(error); return; }
    const s = view.settings;
    const subject = h("input", { type: "text", value: s.email_subject, maxlength: 200 });
    const text = h("textarea", { rows: 8, maxlength: 5000 }, s.email_body);
    const limit = h("input", { type: "number", min: 1, max: 100, value: s.daily_limit });
    const saveLimit = h("button", { type: "button", class: "btn ghost sm", text: "Save limit" });
    saveLimit.addEventListener("click", () => withBusy(saveLimit, async () => { await api("/api/recruiters/outreach", { method: "POST", body: { daily_limit: Number(limit.value) } }); toast("Daily limit saved.", "success"); }));
    const results = h("div", { class: "rcx-preview" });
    const previewButton = h("button", { type: "button", class: "btn secondary sm", text: "Preview emails" });
    const sendButton = h("button", { type: "button", class: "btn sm", text: "Send", disabled: true, id: "rcxSendEmails" });
    let boxes = [];
    const refreshSend = () => { const n = boxes.filter((box) => box.checked).length; sendButton.textContent = `Send ${fmt.plural(n, "email")}`; sendButton.disabled = n === 0 || !view.email.ready; };
    const setup = view.email.ready ? h("p", { class: "muted small", text: `Emails go out from ${view.email.from_email} through your own mail server (Business hub > Email).` }) : h("div", { class: "note warn" }, h("span", { text: "Set up your email first: your own mail server or Gmail with an app password." }), " ", h("a", { href: "#/hub?tab=email", text: "Set up email", onclick: () => closeDialog() }));
    previewButton.addEventListener("click", () => withBusy(previewButton, async () => {
      const plan = await api(`/api/recruiters/lists/${list.list_id}/email/preview`, { method: "POST", body: { subject: subject.value, body: text.value } });
      boxes = [];
      const rows = plan.items.map((item) => {
        const box = item.status === "ready" ? h("input", { type: "checkbox", checked: true, "aria-label": `Email ${item.name}`, dataset: { id: item.recruiter_id } }) : null;
        if (box) { box.addEventListener("change", refreshSend); boxes.push(box); }
        const [label, cls] = STATUS[item.status] || [item.status, ""];
        return h("div", { class: "row rcx-mail" },
          h("label", { class: "check rcx-pick" }, box || h("span", { class: "rcx-nobox" })),
          h("div", { class: "rcx-body" },
            h("span", { class: "title", text: item.name }),
            h("span", { class: "sub", text: item.email || "LinkedIn only" }),
            h("div", { class: "meta" }, h("span", { class: `badge ${cls}`, text: label })),
            item.reason ? h("p", { class: "small muted", text: item.reason }) : null,
            item.body ? h("details", { class: "rcx-letter" }, h("summary", { text: item.subject }), h("pre", { text: item.body })) : null));
      });
      put(results, 
        h("p", { class: "small", role: "status", text: `${plan.ready} ready · ${plan.skipped} skipped · ${plan.later} for tomorrow · ${plan.linkedin_only} LinkedIn only. Sent today: ${plan.sent_today} of ${plan.daily_limit}.` }),
        plan.linkedin_only ? h("p", { class: "muted small", text: "LinkedIn-only contacts have no email address in your records. Monosage does not look addresses up or guess them; use LinkedIn drafts for them." }) : null,
        h("div", { class: "list" }, ...rows));
      refreshSend();
    }));
    sendButton.addEventListener("click", async () => {
      const ids = boxes.filter((box) => box.checked).map((box) => box.dataset.id);
      if (!ids.length) return;
      if (!(await Aira.confirm({ title: `Send ${fmt.plural(ids.length, "email")}?`, message: "Each email goes out as shown in the preview, with the opt-out line at the end. Contacts who replied or opted out since the preview are skipped.", confirmLabel: "Send now" }))) return;
      await withBusy(sendButton, async () => {
        const result = await api(`/api/recruiters/lists/${list.list_id}/email/send`, { method: "POST", body: { subject: subject.value, body: text.value, recruiter_ids: ids, confirm: true } });
        toast(`Sent ${result.sent}${result.failed ? `, ${result.failed} failed` : ""}${result.skipped ? `, ${result.skipped} skipped` : ""}.`, result.failed ? "error" : "success");
        for (const detail of result.details.filter((d) => d.status === "failed")) toast(detail.error, "error");
        previewButton.click();
      });
    });
    const body = h("div", { class: "rcx-flow" },
      h("p", { class: "muted", text: `Each email is written from your template and the contact's details. Every email ends with: “${view.opt_out_line}” Contacts who replied, opted out, are on your do-not-contact list or have no email address are skipped.` }),
      setup,
      h("div", { class: "form-grid" }, ui.field("Subject", subject), ui.field("Emails per day", limit, "Up to 100."), h("div", { class: "field" }, h("label", { text: " " }), saveLimit)),
      h("div", { class: "field wide" }, h("label", { for: "rcxEmailText", text: "Email text" }), Object.assign(text, { id: "rcxEmailText" }), h("span", { class: "hint", text: placeholderHint(view.placeholders) })),
      h("div", { class: "btn-row", style: { marginTop: "10px" } }, previewButton, sendButton),
      results);
    dialog = Aira.modal({ title: `Email the list “${list.name}”`, body, wide: true, actions: [{ label: "Close", kind: "ghost" }] });
  }

  async function linkedinFlow(list) {
    let dialog;
    const closeDialog = () => { if (dialog) dialog.close(); };
    // The templates live with the email settings; without them (the hosted build) the default text is used.
    const view = await api("/api/recruiters/outreach", { silent: true }).catch(() => null);
    const text = h("textarea", { rows: 4, maxlength: 2000 }, view ? view.settings.linkedin_body : "Hi {first_name}, I hope you are well. I am still interested in roles at {company|your company}. Is there anything new you could share? Thank you, {my_first_name}");
    const results = h("div", { class: "rcx-preview" });
    const previewButton = h("button", { type: "button", class: "btn secondary sm", text: "Preview drafts" });
    const saveButton = h("button", { type: "button", class: "btn sm", text: "Save drafts", disabled: true, id: "rcxSaveDrafts" });
    let boxes = [];
    const refresh = () => { const n = boxes.filter((box) => box.checked).length; saveButton.textContent = `Save ${fmt.plural(n, "draft")}`; saveButton.disabled = n === 0; };
    previewButton.addEventListener("click", () => withBusy(previewButton, async () => {
      const plan = await api(`/api/recruiters/lists/${list.list_id}/linkedin/preview`, { method: "POST", body: { body: text.value } });
      boxes = [];
      put(results, 
        h("p", { class: "small", role: "status", text: `${plan.ready} ready · ${plan.skipped} skipped.` }),
        h("div", { class: "list" }, ...plan.items.map((item) => {
          const box = item.status === "ready" ? h("input", { type: "checkbox", checked: true, "aria-label": `Draft for ${item.name}`, dataset: { id: item.recruiter_id } }) : null;
          if (box) { box.addEventListener("change", refresh); boxes.push(box); }
          const [label, cls] = STATUS[item.status] || [item.status, ""];
          return h("div", { class: "row rcx-mail" },
            h("label", { class: "check rcx-pick" }, box || h("span", { class: "rcx-nobox" })),
            h("div", { class: "rcx-body" },
              h("span", { class: "title", text: item.name }),
              h("div", { class: "meta" }, h("span", { class: `badge ${cls}`, text: label })),
              item.reason ? h("p", { class: "small muted", text: item.reason }) : null,
              item.text ? h("pre", { class: "rcx-draft", text: item.text }) : null,
              item.link && item.status !== "ready" ? h("a", { class: "small", href: item.link, text: "Open conversation", onclick: () => closeDialog() }) : null));
        })));
      refresh();
    }));
    saveButton.addEventListener("click", () => withBusy(saveButton, async () => {
      const ids = boxes.filter((box) => box.checked).map((box) => box.dataset.id);
      const result = await api(`/api/recruiters/lists/${list.list_id}/linkedin/drafts`, { method: "POST", body: { body: text.value, recruiter_ids: ids } });
      toast(`Saved ${fmt.plural(result.drafted, "draft")}. Nothing was sent.`, "success");
      put(results, 
        h("p", { class: "small", role: "status", text: "Open each conversation, read the draft and press Send yourself. Monosage never sends these for you." }),
        h("ul", { class: "inline-list rcx-drafted" }, ...result.drafts.map((d) => h("li", {}, h("a", { href: d.link, text: `${d.name}: open conversation`, onclick: () => closeDialog() })))));
      boxes = [];
      refresh();
    }));
    const body = h("div", { class: "rcx-flow" },
      h("p", { class: "muted", text: "Monosage writes a draft into each contact's LinkedIn conversation. Nothing is sent: you open each conversation in Messages, read the draft and press Send yourself, one at a time. Contacts with no saved conversation, a draft already waiting, or on your do-not-contact list are skipped." }),
      h("div", { class: "field wide" }, h("label", { for: "rcxLinkedinText", text: "Message" }), Object.assign(text, { id: "rcxLinkedinText" }), h("span", { class: "hint", text: placeholderHint(view ? view.placeholders : ["first_name", "name", "company", "title", "job", "last_contact", "my_name", "my_first_name"]) })),
      h("div", { class: "btn-row", style: { marginTop: "10px" } }, previewButton, saveButton),
      results);
    dialog = Aira.modal({ title: `LinkedIn drafts for “${list.name}”`, body, wide: true, actions: [{ label: "Close", kind: "ghost" }] });
  }

  // -------------------------------------------------------------------------
  // Contact story: one timeline, an optional AI summary, do not contact
  // -------------------------------------------------------------------------
  const KIND_LABELS = { linkedin: "LinkedIn", email: "Email", interview: "Interview", job: "Job", interaction: "Logged", outreach: "Sent email", linkedin_draft: "LinkedIn draft" };
  const OUTBOX_STATUS = { sent: "Sent", failed: "Failed", approved: "Sending", pending: "Waiting for approval", cancelled: "Cancelled" };

  Aira.renderRecruiterStory = async (host, recruiterId, refresh) => {
    put(host, h("div", { class: "card rcx-story" }, h("h3", { text: "Contact story" }), ui.skeleton(3)));
    let data;
    try { data = await api(`/api/recruiters/${recruiterId}/story`); } catch (error) { put(host, h("div", { class: "card" }, h("h3", { text: "Contact story" }), ui.error(error))); return; }
    const summaryBox = h("div", { class: "rcx-summary" });
    const drawSummary = (summary, message, ready) => {
      const button = h("button", { type: "button", class: "btn secondary sm", text: summary ? "Summarize again" : "Summarize with AI", disabled: !ready || !data.items.length });
      button.addEventListener("click", () => withBusy(button, async () => {
        const result = await api(`/api/recruiters/${recruiterId}/story/summary`, { method: "POST", body: {} });
        drawSummary(result.summary || summary, result.message, result.model_ready);
        if (result.message) toast(result.message, "error");
      }));
      put(summaryBox, 
        summary ? h("div", { class: "rcx-summary-text" },
          h("p", {}, h("strong", { text: "Relationship: " }), summary.relationship || "—"),
          h("p", {}, h("strong", { text: "Next step: " }), summary.next_step || "—"),
          h("small", { class: "muted", text: summary.stale ? "Written before the latest changes to this story." : `Written ${fmt.ago(summary.created_at)} by AI from the timeline below.` })) : null,
        !ready ? h("p", { class: "muted small", text: "No AI model is set up, so there is no summary. The timeline below is complete without one." }) : null,
        message && ready ? h("p", { class: "muted small", text: message }) : null,
        h("div", { class: "btn-row" }, button));
    };
    drawSummary(data.summary, "", data.model_ready);
    const removeInteraction = async (event, id) => {
      if (!(await Aira.confirm({ title: "Remove this interaction?", message: "", confirmLabel: "Remove", danger: true }))) return;
      await withBusy(event.currentTarget, async () => { await api(`/api/interactions/${id}`, { method: "DELETE" }); refresh(); });
    };
    const items = data.items.map((item) => {
      const who = item.direction === "inbound" ? "from them" : item.direction === "outbound" ? "from you" : "";
      const status = item.kind === "outreach" ? OUTBOX_STATUS[item.status] || item.status : item.kind === "interview" ? item.status : item.kind === "job" ? fmt.stage(item.stage) : "";
      const action = item.kind === "job" && item.job_id ? h("a", { href: "#", class: "small", text: "Open job", onclick: (event) => { event.preventDefault(); Aira.openJob(item.job_id); } })
        : item.kind === "interaction" ? h("button", { type: "button", class: "btn ghost sm", text: "Remove", onclick: (event) => removeInteraction(event, item.interaction_id) })
        : item.link ? h("a", { href: item.link, class: "small", text: item.kind === "interview" ? "Open interview" : item.kind === "email" ? "Email updates" : "Open conversation", onclick: () => Aira.closeDrawer() }) : null;
      return h("li", { class: `rcx-item rcx-${item.kind}` },
        h("strong", { text: [KIND_LABELS[item.kind] || item.kind, who].filter(Boolean).join(" · ") }),
        h("div", { class: "rcx-item-title", text: item.title }),
        item.text ? h("div", { class: "rcx-item-text", text: item.text }) : null,
        item.error ? h("div", { class: "small danger-text", text: item.error }) : null,
        h("small", { text: [item.at ? fmt.date(item.at) : "No date", status, item.job_title].filter(Boolean).join(" · ") }),
        action);
    });
    const blocked = data.do_not_contact;
    const dncButton = blocked
      ? h("button", { type: "button", class: "btn ghost sm", text: "Allow contact again", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/recruiters/${recruiterId}/do-not-contact`, { method: "DELETE" }); toast("Contact allowed again.", "success"); Aira.renderRecruiterStory(host, recruiterId, refresh); }) })
      : h("button", { type: "button", class: "btn ghost sm danger", text: "Do not contact", onclick: async (event) => { if (await Aira.confirm({ title: "Never write to this contact?", message: "Lists skip them for email and LinkedIn drafts. Their email address also goes on the Leads do-not-contact list.", confirmLabel: "Do not contact", danger: true })) await withBusy(event.currentTarget, async () => { await api(`/api/recruiters/${recruiterId}/do-not-contact`, { method: "POST", body: { reason: "Your choice" } }); toast("Added to your do-not-contact list.", "success"); Aira.renderRecruiterStory(host, recruiterId, refresh); }); } });
    put(host, h("div", { class: "card rcx-story" },
      h("div", { class: "panel-head" }, h("h3", { text: "Contact story" }), h("small", { class: "muted", text: fmt.plural(data.items.length, "event") })),
      blocked ? h("p", { class: "note warn", text: `On your do-not-contact list${blocked.reason ? `: ${blocked.reason}` : ""}. Lists never write to this contact.` }) : null,
      summaryBox,
      data.sources.length ? h("div", { class: "meta rcx-found" }, h("span", { class: "small muted", text: "Found in:" }), ...sourceBadges([...new Set(data.sources.map((source) => source.kind))])) : null,
      items.length ? h("ul", { class: "timeline rcx-timeline" }, ...items) : h("p", { class: "muted", text: "Nothing yet. LinkedIn messages, emails, interviews, jobs and logged interactions with this contact show here." }),
      h("div", { class: "btn-row", style: { marginTop: "10px" } }, dncButton)));
  };
})();

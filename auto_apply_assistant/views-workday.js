/* Monosage — Settings > Integrations: Workday accounts (one candidate account per employer's Workday site) */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, withBusy } = Aira;
  const BASE = "/api/autopilot/workday-accounts";

  // Settings asks every registered file for its cards: Aira.settingsCards("integrations") -> [elements].
  const previous = Aira.settingsCards;
  Aira.settingsCards = (tab) => [...(previous ? previous(tab) : []), ...(tab === "integrations" && !Aira.cloud ? [workdayCard()] : [])];

  function workdayCard() {
    const body = h("div", {}, ui.skeleton(2));
    const card = h("div", { class: "card", id: "workdayAccounts" },
      h("h3", { text: "Workday accounts" }),
      h("p", { class: "muted", text: "Workday gives every employer its own site and its own candidate account. Save the account you have for a site, or let Monosage create one with your profile email and a strong password it keeps encrypted. Passwords never come back to this page." }),
      body);
    load(body);
    return card;
  }

  async function load(body) {
    let data;
    try { data = await api(BASE, { silent: true }); } catch (error) { body.replaceChildren(ui.error(error, () => load(body))); return; }
    const auto = h("input", { type: "checkbox", checked: Boolean(data.auto_create) });
    auto.addEventListener("change", async () => {
      auto.disabled = true;
      try { await api("/api/autopilot/workday-settings", { method: "POST", body: { auto_create: auto.checked } }); toast(auto.checked ? "Monosage will create an account on new Workday sites." : "New Workday sites will wait for an account you add.", "success"); }
      catch (error) { auto.checked = !auto.checked; Aira.reportError(error); }
      finally { auto.disabled = false; }
    });
    const list = data.accounts.length
      ? h("ul", { class: "wd-accounts", style: { listStyle: "none", padding: 0, margin: "10px 0" } }, ...data.accounts.map((account) => accountRow(account, body)))
      : h("p", { class: "muted", style: { margin: "10px 0" }, text: "No Workday accounts saved yet." });
    body.replaceChildren(
      h("label", { class: "check", style: { marginTop: "6px" } }, auto, "Create an account for me on new Workday sites (uses your profile email; you may need to verify it)"),
      list,
      addForm(body),
      h("p", { class: "muted", style: { fontSize: "12px", marginTop: "8px" }, text: "If Workday sends a verification email or shows a CAPTCHA, the application waits under Needs you. Monosage never bypasses these." }),
    );
  }

  function accountRow(account, body) {
    const tone = account.state === "active" ? "ok" : account.state === "new" ? "" : "warn";
    const edit = h("button", { type: "button", class: "btn ghost sm", text: "Change password" });
    const remove = h("button", { type: "button", class: "btn ghost sm", text: "Remove" });
    const row = h("li", { style: { padding: "8px 0", borderTop: "1px solid var(--line, #e5e7eb)" } },
      h("div", { style: { display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center", justifyContent: "space-between" } },
        h("div", { style: { minWidth: 0, overflowWrap: "anywhere" } },
          h("strong", { text: account.host }),
          h("div", { class: "muted", style: { fontSize: "13px" }, text: `${account.email} · ${account.origin === "generated" ? "created by Monosage" : "your account"}${account.last_used_at ? ` · used ${fmt.ago(account.last_used_at)}` : ""}` })),
        h("div", { class: "btn-row", style: { margin: 0 } }, h("span", { class: `badge ${tone}`, text: account.state_label }), edit, remove)),
      account.message ? h("p", { class: "muted", style: { fontSize: "12px", margin: "4px 0 0" }, text: account.message }) : null);
    edit.addEventListener("click", () => {
      const password = h("input", { type: "password", autocomplete: "new-password", maxlength: 256, placeholder: "New password" });
      const save = h("button", { type: "button", class: "btn sm", text: "Save" });
      save.addEventListener("click", () => withBusy(save, async () => {
        if (!password.value) { toast("Enter the new password.", "error"); return; }
        await api(`${BASE}/${encodeURIComponent(account.host)}`, { method: "PATCH", body: { password: password.value } });
        password.value = "";
        toast(`Password saved for ${account.host}.`, "success");
        load(body);
      }));
      row.append(h("div", { class: "btn-row", style: { marginTop: "6px" } }, password, save));
      edit.disabled = true;
      password.focus();
    });
    remove.addEventListener("click", async () => {
      if (!(await Aira.confirm({ title: `Remove ${account.host}?`, message: "Monosage forgets this email and password. The account on the employer's Workday site stays.", confirmLabel: "Remove", danger: true }))) return;
      await withBusy(remove, async () => { await api(`${BASE}/${encodeURIComponent(account.host)}`, { method: "DELETE" }); toast("Account removed.", "success"); load(body); });
    });
    return row;
  }

  function addForm(body) {
    const host = h("input", { type: "text", maxlength: 300, placeholder: "acme.wd5.myworkdayjobs.com", autocomplete: "off" });
    const mode = ui.select([["mine", "I have an account"], ["create", "Create the account for me"]], "mine");
    const email = h("input", { type: "email", maxlength: 254, autocomplete: "off", placeholder: "you@example.com" });
    const password = h("input", { type: "password", maxlength: 256, autocomplete: "new-password" });
    const emailField = ui.field("Email", email);
    const passwordField = ui.field("Password", password);
    const sync = () => { const mine = mode.value === "mine"; passwordField.hidden = !mine; email.placeholder = mine ? "you@example.com" : "Your profile email"; };
    mode.addEventListener("change", sync);
    sync();
    const add = h("button", { type: "button", class: "btn secondary sm", text: "Add account" });
    add.addEventListener("click", () => withBusy(add, async () => {
      const create = mode.value === "create";
      const payload = { host: host.value.trim(), create_for_me: create };
      if (email.value.trim()) payload.email = email.value.trim();
      if (!create) payload.password = password.value;
      await api(BASE, { method: "POST", body: payload });
      password.value = "";
      toast(create ? "Saved. Monosage creates the account the next time it applies there." : "Workday account saved.", "success");
      load(body);
    }));
    return h("details", { style: { marginTop: "6px" } },
      h("summary", { text: "Add a Workday account" }),
      h("div", { class: "form-grid", style: { marginTop: "8px" } }, ui.field("Workday site", host, "The address of the employer's job site, or any link on it"), ui.field("Account", mode), emailField, passwordField),
      h("div", { class: "btn-row" }, add));
  }

})();

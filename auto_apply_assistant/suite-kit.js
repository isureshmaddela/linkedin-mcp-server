/* Aira — the industry apps' UI kit (Aira.suite).
 *
 * Every industry app (views-studio.js, views-bookings.js, ...) builds its screens from these pieces so the apps look
 * and behave alike: a page head, tabs kept in the address (?tab=), headline numbers, tables, forms in a modal,
 * status pills, a public-link card (copy, open, QR-free share), a setup checklist, an activity list, bar charts
 * and money in integer cents. DOM is built with h() and textContent only: nothing the server sends is parsed as
 * HTML. Errors from the API surface as a toast (Aira.api) and, in forms, inline.
 *
 * Contract (see docs/suite-sdk.md):
 *   S.api(app, path, opts)            Aira.api(`/api/${app}${path}`, opts)
 *   S.page(main, {title, subtitle, actions})  -> the body element under the page head
 *   S.tabs(body, route, section, items, active) -> renders a tab bar that navigates #/<section>?tab=<id>
 *   S.kpis(cards)                     cards: [{label, value, hint, tone: "good"|"warn"|"bad"|""}]
 *   S.table({columns, rows, onRow, empty, actions})  columns: [{key, label, render(row), num}]
 *   S.form(fields, values) -> {node, read(), setError(msg)}   fields: [{name, label, type, options, required, hint, placeholder, rows, step, min, max}]
 *   S.edit({title, fields, values, submitLabel, onSave(values)}) -> modal form; resolves with onSave's result or null
 *   S.pill(status, tones)             tones: {status: "good"|"warn"|"bad"|"info"}
 *   S.money(cents, currency) / S.toCents(text) / S.fromCents(cents)
 *   S.share({title, link, onCreate, onRevoke}) -> a card with the public link and actions
 *   S.checklist(steps)                steps: [{label, done, hint}]
 *   S.activity(rows)                  rows from /api/suite/activity
 *   S.bars(series, {format})          series: [{label, value}]
 *   S.section(title, ...children)     a titled card
 *   S.grid(...children)               a responsive two-column grid
 *   S.button(label, onClick, {kind, icon}) / S.confirmDelete(what) / S.copy(text) / S.absolute(path)
 *   S.business()                      the cached business profile (currency, name, ...)
 */
"use strict";
(() => {
  const root = typeof window !== "undefined" ? window : globalThis;
  const Aira = root.Aira || (root.Aira = { views: {} });
  const h = (...args) => root.h(...args);
  const S = (Aira.suite = Aira.suite || {});

  S.api = (app, path, opts) => Aira.api(`/api/${app}${path}`, opts);

  // ---------------------------------------------------------------- money
  const SYMBOLS = { USD: "$", EUR: "€", GBP: "£", INR: "₹", CAD: "CA$", AUD: "A$", SGD: "S$", AED: "AED ", JPY: "¥" };
  S.money = (cents, currency) => {
    const code = currency || (S._business && S._business.currency) || "USD";
    const symbol = SYMBOLS[code] || `${code} `;
    const value = Number(cents || 0) / 100;
    if (code === "JPY") return `${symbol}${Math.round(value).toLocaleString()}`;
    return `${value < 0 ? "-" : ""}${symbol}${Math.abs(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };
  S.fromCents = (cents) => (cents === null || cents === undefined || cents === "" ? "" : (Number(cents) / 100).toFixed(2));
  S.toCents = (text) => {
    const raw = String(text === undefined || text === null ? "" : text).replace(/[^0-9.\-]/g, "");
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? Math.round(value * 100) : null;
  };

  S._business = null;
  S.business = async (refresh = false) => {
    if (!S._business || refresh) {
      try { S._business = (await Aira.api("/api/suite/business", { silent: true })).business; } catch { S._business = { currency: "USD", name: "" }; }
    }
    return S._business;
  };

  // ---------------------------------------------------------------- layout
  S.page = (main, { title, subtitle, actions = [] } = {}) => {
    const body = h("div", { class: "suite-body" });
    main.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: title || "" }), subtitle ? h("p", { text: subtitle }) : null), actions.filter(Boolean).length ? h("div", { class: "btn-row" }, ...actions.filter(Boolean)) : null),
      body,
    );
    return body;
  };

  S.tabs = (container, route, section, items, active) => {
    const current = active || (route && route.query && route.query.tab) || items[0].id;
    const bar = Aira.ui.tabs(items, current, (id) => Aira.router.go([section], { tab: id }));
    container.append(bar);
    // The left menu lists them too, like a mail app's folders (shell.js).
    if (Aira.shell) Aira.shell.tabs(section, items, current);
    return current;
  };

  S.section = (title, ...children) => h("section", { class: "card suite-section" }, title ? h("h2", { class: "suite-h2", text: title }) : null, ...children.filter(Boolean));
  S.grid = (...children) => h("div", { class: "suite-grid" }, ...children.filter(Boolean));

  S.button = (label, onClick, { kind = "", icon = "", title = "" } = {}) => {
    const button = h("button", { type: "button", class: `btn ${kind}`.trim(), title: title || undefined }, icon ? Aira.icon(icon) : null, h("span", { text: label }));
    button.addEventListener("click", (event) => Aira.withBusy(event.currentTarget, () => onClick(event)));
    return button;
  };

  S.kpis = (cards) => h("div", { class: "suite-kpis" }, ...cards.filter(Boolean).map((card) => h("div", { class: `suite-kpi ${card.tone || ""}` }, h("span", { class: "suite-kpi-label", text: card.label }), h("strong", { class: "suite-kpi-value", text: String(card.value === undefined || card.value === null ? "-" : card.value) }), card.hint ? h("span", { class: "suite-kpi-hint", text: card.hint }) : null)));

  const PILL_TONES = { paid: "good", active: "good", accepted: "good", confirmed: "good", completed: "good", delivered: "good", sent: "info", published: "good", live: "good", checked_in: "good", draft: "", pending: "warn", open: "warn", overdue: "bad", cancelled: "bad", declined: "bad", no_show: "bad", failed: "bad", refunded: "bad", archived: "" };
  S.pill = (status, tones = {}) => {
    const key = String(status || "").toLowerCase();
    const tone = tones[key] !== undefined ? tones[key] : PILL_TONES[key] || "";
    return h("span", { class: `suite-pill ${tone}`, text: key.replaceAll("_", " ") || "-" });
  };

  S.table = ({ columns, rows, onRow, empty, caption } = {}) => {
    if (!rows || !rows.length) return empty instanceof Node ? empty : Aira.ui.empty(empty || "Nothing here yet", "");
    const head = h("tr", {}, ...columns.map((column) => h("th", { class: column.num ? "num" : "", scope: "col", text: column.label })));
    const body = rows.map((row) => {
      const tr = h("tr", { class: onRow ? "suite-row" : "" }, ...columns.map((column) => {
        const value = column.render ? column.render(row) : row[column.key];
        return h("td", { class: column.num ? "num" : "" }, value instanceof Node ? value : String(value === undefined || value === null ? "" : value));
      }));
      if (onRow) {
        tr.tabIndex = 0;
        tr.addEventListener("click", (event) => { if (!event.target.closest("button, a, input, select")) onRow(row); });
        // Only Enter on the row itself: Enter on a button, link or box inside the row does that control's own job.
        tr.addEventListener("keydown", (event) => { if (event.key === "Enter" && event.target === tr) { event.preventDefault(); onRow(row); } });
      }
      return tr;
    });
    return h("div", { class: "table-wrap" }, h("table", { class: "table suite-table" }, caption ? h("caption", { class: "sr-only", text: caption }) : null, h("thead", {}, head), h("tbody", {}, ...body)));
  };

  // ---------------------------------------------------------------- forms
  function input(field, value) {
    const type = field.type || "text";
    const common = { name: field.name, required: !!field.required, placeholder: field.placeholder || "" };
    let node;
    if (type === "textarea") node = h("textarea", { ...common, rows: field.rows || 4 });
    else if (type === "select") node = Aira.ui.select(field.options || [], value === undefined || value === null ? "" : String(value), common);
    else if (type === "checkbox") node = h("input", { ...common, type: "checkbox", checked: !!value });
    else if (type === "money") node = h("input", { ...common, type: "text", inputmode: "decimal", placeholder: field.placeholder || "0.00" });
    else node = h("input", { ...common, type, step: field.step, min: field.min, max: field.max });
    if (type === "money") node.value = S.fromCents(value);
    else if (type !== "checkbox" && type !== "select") node.value = value === undefined || value === null ? "" : String(value);
    return node;
  }
  S.form = (fields, values = {}) => {
    const inputs = {};
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const node = h("div", { class: "suite-form" }, ...fields.map((field) => {
      if (field.type === "heading") return h("h3", { class: "suite-h3", text: field.label });
      const control = input(field, values[field.name] !== undefined ? values[field.name] : field.value);
      inputs[field.name] = { field, control };
      if (field.type === "checkbox") return h("label", { class: "suite-check" }, control, h("span", { text: field.label }), field.hint ? h("span", { class: "hint", text: field.hint }) : null);
      return Aira.ui.field(field.label + (field.required ? " *" : ""), control, field.hint);
    }), error);
    const read = () => {
      const out = {};
      for (const [name, { field, control }] of Object.entries(inputs)) {
        if (field.type === "checkbox") out[name] = control.checked;
        else if (field.type === "money") {
          const cents = S.toCents(control.value);
          if (cents === null && control.value.trim()) throw new Error(`Enter ${field.label.toLowerCase()} as a number, like ${field.example || "4.99"}.`);
          out[name] = cents;
        }
        else if (field.type === "number") out[name] = control.value === "" ? null : Number(control.value);
        else out[name] = control.value.trim();
      }
      return out;
    };
    const setError = (message) => { error.textContent = message || ""; error.hidden = !message; };
    return { node, read, setError, inputs };
  };

  S.edit = ({ title, fields, values = {}, submitLabel = "Save", onSave, wide = false }) => new Promise((resolve) => {
    const form = S.form(fields, values);
    let done = false;
    let dialog = null;
    const finish = (result) => { if (!done) { done = true; resolve(result); } };
    // Enter in a one-line field saves, like pressing the main button (a hidden submit button makes Enter submit).
    const submit = (event) => { event.preventDefault(); const primary = dialog && dialog.box.querySelector(".actions .btn.primary"); if (primary && !primary.disabled) primary.click(); };
    dialog = Aira.modal({
      title,
      wide,
      body: h("form", { onsubmit: submit }, form.node, h("button", { type: "submit", class: "sr-only", tabindex: "-1", "aria-hidden": "true" })),
      onClose: () => finish(null),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: submitLabel, kind: "primary", onClick: async () => {
          form.setError("");
          for (const { field, control } of Object.values(form.inputs)) {
            if (field.required && field.type !== "checkbox" && !String(control.value || "").trim()) { form.setError(`Enter ${field.label.toLowerCase()}.`); control.focus(); return false; }
          }
          try {
            const result = await onSave(form.read());
            finish(result === undefined ? true : result);
            return true; // the modal closes itself
          } catch (error) { form.setError(error && error.message ? error.message : String(error)); return false; }
        } },
      ],
    });
  });

  S.confirmDelete = (what) => Aira.confirm({ title: `Delete ${what}`, message: `Delete ${what}? This cannot be undone.`, confirmLabel: "Delete", danger: true });

  // ---------------------------------------------------------------- sharing
  S.absolute = (path) => `${location.origin}${path}`;
  S.copy = async (text) => {
    try { await navigator.clipboard.writeText(text); Aira.toast("Copied.", "success"); } catch { Aira.toast(text); }
  };
  S.share = ({ title = "Public link", text = "", link, onCreate, onRevoke } = {}) => {
    const card = h("div", { class: "suite-share" });
    const render = (current) => {
      if (!current) {
        card.replaceChildren(...[h("div", {}, h("strong", { text: title }), h("p", { class: "muted", text: text || "Create a link to share with customers. Anyone with the link can open the page." })), onCreate ? S.button("Create link", async () => render(await onCreate()), { kind: "secondary" }) : null].filter(Boolean));
        return;
      }
      const url = S.absolute(current.path);
      card.replaceChildren(
        h("div", { class: "suite-share-main" }, h("strong", { text: title }), h("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "suite-share-url", text: url }), h("span", { class: "muted", text: `${current.views || 0} views · ${current.actions || 0} actions` })),
        h("div", { class: "btn-row" },
          S.button("Copy", () => S.copy(url), { kind: "secondary sm" }),
          h("a", { class: "btn secondary sm", href: url, target: "_blank", rel: "noopener noreferrer", text: "Open" }),
          onRevoke ? S.button("Turn off", async () => { if (await Aira.confirm({ title: "Turn off this link", message: "People with the link will no longer be able to open it. You can create a new link later.", confirmLabel: "Turn off", danger: true })) { await onRevoke(); render(null); } }, { kind: "ghost sm" }) : null),
      );
    };
    render(link);
    return card;
  };

  // ---------------------------------------------------------------- engagement widgets
  S.checklist = (steps = []) => {
    const done = steps.filter((step) => step.done).length;
    return h("div", { class: "suite-checklist" },
      h("div", { class: "suite-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(steps.length), "aria-valuenow": String(done) }, h("span", { style: { width: `${steps.length ? Math.round((done * 100) / steps.length) : 0}%` } })),
      h("ol", {}, ...steps.map((step) => h("li", { class: step.done ? "done" : "" }, h("span", { class: "suite-tick", "aria-hidden": "true", text: step.done ? "✓" : "" }), h("span", {}, h("strong", { text: step.label }), step.hint && !step.done ? h("span", { class: "muted", text: ` ${step.hint}` }) : null)))));
  };

  S.activity = (rows = []) => {
    if (!rows.length) return Aira.ui.empty("No activity yet", "What you and your customers do shows up here.");
    return h("ul", { class: "suite-activity" }, ...rows.map((row) => h("li", {},
      h("span", { class: `suite-dot ${row.public ? "public" : ""}`, "aria-hidden": "true" }),
      h("div", {}, h("strong", { text: row.title }), row.detail ? h("span", { class: "muted", text: ` ${row.detail}` }) : null, h("div", { class: "muted small", text: `${row.app} · ${Aira.fmt.ago(row.at)}${row.amount_cents ? ` · ${S.money(row.amount_cents)}` : ""}${row.public ? " · by a customer" : ""}` })))));
  };

  S.bars = (series = [], { format = (v) => String(v) } = {}) => {
    const max = Math.max(0, ...series.map((item) => Number(item.value) || 0)) || 1; // small values (rates, cents) still show
    return h("div", { class: "suite-bars", role: "img", "aria-label": series.map((item) => `${item.label}: ${format(item.value)}`).join(", ") },
      ...series.map((item) => h("div", { class: "suite-bar" }, h("span", { class: "suite-bar-fill", style: { height: `${Math.round(((Number(item.value) || 0) * 100) / max)}%` }, title: format(item.value) }), h("span", { class: "suite-bar-label", text: item.label }))));
  };

  S.statusOptions = (statuses) => statuses.map((status) => [status, status.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase())]);
  S.when = (value) => (value ? Aira.fmt.datetime(value) : "");
  S.day = (value) => (value ? Aira.fmt.date(value) : "");

  if (typeof module !== "undefined" && module.exports) module.exports = { toCents: S.toCents, fromCents: S.fromCents };
})();

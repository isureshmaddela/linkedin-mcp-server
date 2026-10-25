/* Aira hosted build — plan, billing and usage screens.
 *
 * Owners and billing contacts manage the plan (#/billing); administrators and auditors see the same page as
 * a read-only "Usage" view (#/usage). Everything here is a view over /api/org/billing, /api/org/usage and
 * /api/org/billing/* — the server enforces who may do what; the page only hides what it would refuse.
 */
"use strict";

(function () {
  const { h } = window;
  const cloud = Aira.cloud;
  if (!cloud) return;
  const { fmt, ui } = Aira;

  const PLAN_ORDER = ["trial", "starter", "team", "enterprise"];
  const DAY = 86400;
  const SUPPORT = { community: "Community", email: "Email", email_1bd: "Email, 1 business day", named_contact_sla: "Named contact, SLA" };

  // ---------------------------------------------------------------------------
  // Pure helpers (also covered by tests/js/cloud_billing.test.mjs)
  // ---------------------------------------------------------------------------
  const titleCase = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "");
  const money = (cents, currency) => (Number(cents || 0) / 100).toLocaleString([], { style: "currency", currency: String(currency || "usd").toUpperCase() });
  const num = (value) => Number(value).toLocaleString();
  const bytes = (value) => {
    const n = Number(value);
    if (n >= 1024 ** 3) return `${+(n / 1024 ** 3).toFixed(1)} GB`;
    if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`;
    return `${Math.round(n / 1024)} KB`;
  };
  const retention = (days) => (days >= 365 ? `${+(days / 365).toFixed(1)} year${days >= 730 ? "s" : ""}` : `${days} days`);
  const rank = (plan) => PLAN_ORDER.indexOf(plan);

  /** What a subscription state means to a person, with the dates that matter. */
  function statusInfo(b, now = Date.now() / 1000) {
    const date = (seconds) => (seconds ? fmt.date(seconds) : "—");
    const days = (until) => Math.max(0, Math.ceil((until - now) / DAY));
    const info = { label: (b.status || "").replace("_", " "), level: "", text: "" };
    switch (b.status) {
      case "trialing":
        info.level = "info";
        info.text = `Trial: ${days(b.trial_end || b.period_end)} day${days(b.trial_end || b.period_end) === 1 ? "" : "s"} left (ends ${date(b.trial_end || b.period_end)}). Pick a plan to keep automation running.`;
        break;
      case "active":
        info.level = "ok";
        info.text = b.cancel_at_period_end ? `Cancels on ${date(b.period_end)}. Service continues until then.` : `Renews on ${date(b.period_end)}.`;
        break;
      case "past_due":
        info.level = "danger";
        info.text = `The last payment failed. Automation is stopped and data stays readable. The organization becomes read-only on ${date((b.past_due_since || now) + 14 * DAY)} unless payment is updated.`;
        break;
      case "read_only":
        info.level = "danger";
        info.text = `Payment is overdue, so data is read-only and automation is stopped.${b.deletion_scheduled_at ? ` Deletion is scheduled for ${date(b.deletion_scheduled_at)}.` : ""}`;
        break;
      case "cancelled":
        info.level = "warn";
        info.text = `The subscription ended. Data is read-only and can be exported until ${date(b.export_until)}.`;
        break;
      default:
        info.text = "";
    }
    if (b.deletion_scheduled_at && b.status !== "read_only") info.text += ` Scheduled for deletion on ${date(b.deletion_scheduled_at)}.`;
    return info;
  }

  /** Limits the organization would exceed on a plan, from what it uses now (a preview; the server decides). */
  function exceedPreview(billing, plan, seats) {
    const out = [];
    const usage = billing.seat_usage || {};
    const seatLimit = Math.min(seats, plan.limits.candidate_seats == null ? seats : plan.limits.candidate_seats);
    if ((usage.candidates || 0) > seatLimit) out.push(`candidate seats (${usage.candidates} in use, ${seatLimit} allowed)`);
    if (plan.limits.staff_seats != null && (usage.staff || 0) > plan.limits.staff_seats) out.push(`coach and admin seats (${usage.staff} in use, ${plan.limits.staff_seats} allowed)`);
    return out;
  }

  function direction(current, currentSeats, target, targetSeats) {
    if (rank(target) > rank(current)) return "up";
    if (rank(target) < rank(current)) return "down";
    if (targetSeats > currentSeats) return "up";
    if (targetSeats < currentSeats) return "down";
    return "same";
  }

  const LIMIT_ROWS = [
    ["Candidate seats", (p) => (p.plan_id === "enterprise" ? "Custom" : `up to ${num(p.limits.candidate_seats)}`)],
    ["Coach and admin seats", (p) => (p.plan_id === "enterprise" ? "Custom" : num(p.limits.staff_seats))],
    ["Applications per candidate per day", (p) => (p.plan_id === "enterprise" ? "Custom" : num(p.limits.apps_per_candidate_day))],
    ["Applications per organization per month", (p) => (p.plan_id === "enterprise" ? "Custom" : num(p.limits.apps_per_org_month))],
    ["Job sources", (p) => ["aira_jobs", "company_boards", "google_jobs", "linkedin"].filter((s) => p.features.includes(`sources.${s}`)).map((s) => ({ aira_jobs: "Aira Jobs", company_boards: "Company boards", google_jobs: "Google Jobs (own SerpApi key)", linkedin: "LinkedIn (own session)" }[s])).join(", ")],
    ["Automatic submission", (p) => (p.features.includes("automation.submit") ? "Yes" : "Review mode only")],
    ["Search plans per candidate", (p) => (p.plan_id === "enterprise" ? "Custom" : num(p.limits.search_plans_per_candidate))],
    ["AI cover letters per month", (p) => (p.plan_id === "enterprise" ? "Custom, or your own model endpoint" : p.features.includes("ai.cover_letters") ? num(p.limits.cover_letters_per_month) : "—")],
    ["API (console and /v1)", (p) => (p.features.includes("api.v1") ? (p.plan_id === "enterprise" ? "Custom" : `${num(p.limits.api_searches_per_month)} searches, /v1 with ${p.limits.api_v1_keys} keys`) : p.features.includes("api.searches") ? `${num(p.limits.api_searches_per_month)} searches, no /v1` : "Read-only history")],
    ["Monitors and alerts per candidate", (p) => (p.plan_id === "enterprise" ? "Custom" : num(p.limits.monitors_per_candidate))],
    ["Document storage and retention", (p) => (p.plan_id === "enterprise" ? "Custom, legal hold" : `${bytes(p.limits.storage_bytes)}, ${p.plan_id === "trial" ? `${p.limits.retention_days} days after the trial ends` : retention(p.limits.retention_days)}`)],
    ["Audit log", (p) => (p.plan_id === "enterprise" ? "7 years, export to SIEM" : retention(p.limits.audit_days))],
    ["Coach scopes", (p) => ["view", "act", "approve"].slice(0, ["view", "act", "approve"].indexOf(p.limits.coach_max_scope) + 1).join(", ")],
    ["Sign-in", (p) => ["Password", "MFA", p.features.includes("auth.sso") ? "SSO (SAML, OIDC)" : "", p.features.includes("auth.scim") ? "SCIM" : ""].filter(Boolean).join(", ")],
    ["Support", (p) => SUPPORT[p.limits.support] || titleCase(String(p.limits.support || ""))],
  ];
  function priceCell(p) {
    if (p.contact_sales) return "Contact sales";
    if (!p.seat_cents_month) return "Free for the trial";
    return `${money(p.seat_cents_month, p.currency)} per candidate seat per month${p.min_seats ? `, from ${p.min_seats} seats` : ""}`;
  }
  function overageCell(p) {
    const o = p.overage || {};
    const parts = [["application_cents", "application"], ["search_cents", "search"], ["cover_letter_cents", "cover letter"], ["pooled_search_cents", "pooled search"]].filter(([k]) => o[k]).map(([k, name]) => `${money(o[k], p.currency)} per extra ${name}`);
    return parts.length ? parts.join("; ") : p.plan_id === "starter" ? "Stops at the limit" : "—";
  }
  window.AiraBillingLogic = { statusInfo, exceedPreview, direction, money, bytes, retention, LIMIT_ROWS, priceCell, overageCell, rank };

  // ---------------------------------------------------------------------------
  // The page
  // ---------------------------------------------------------------------------
  const meter = (label, used, limit, { unit = "", link = "", note = "" } = {}) => {
    const pct = limit ? Math.min(100, Math.round((100 * used) / limit)) : 0;
    const level = limit && pct >= 100 ? "full" : limit && pct >= 80 ? "high" : "";
    const text = limit ? `${unit === "bytes" ? bytes(used) : num(used)} of ${unit === "bytes" ? bytes(limit) : num(limit)}` : `${unit === "bytes" ? bytes(used) : num(used)}${limit === null ? " (no limit)" : ""}`;
    return h("div", { class: `cb-meter ${level}` }, h("div", { class: "cb-meter-top" }, link ? h("a", { href: link, text: label }) : h("span", { text: label }), h("strong", { text: text })), limit ? h("div", { class: "cb-bar", role: "progressbar", "aria-label": label, "aria-valuemin": "0", "aria-valuemax": String(limit), "aria-valuenow": String(Math.min(used, limit)) }, h("i", { style: { width: `${pct}%` } })) : null, note ? h("div", { class: "muted", text: note }) : null);
  };

  async function refreshIdentity() {
    try {
      cloud.me = await Aira.api("/api/auth/me", { silent: true });
      cloud.buildNav();
      cloud.refreshBanners();
    } catch { /* the page reloads its own data next */ }
  }

  async function loadAll(manage) {
    const billing = await Aira.api("/api/org/billing");
    let trial = null;
    try { trial = (await Aira.api("/api/billing/trial-plan", { silent: true })).plan; } catch { /* the table just omits the trial column */ }
    const usage = await Aira.api("/api/org/usage").catch(() => null);
    return { billing: billing.billing, plans: billing.plans, invoices: billing.invoices || [], canManage: !!billing.can_manage && manage, usage, trial };
  }

  async function view(stage, route) {
    const manage = cloud.can("billing.manage");
    const wanted = route.path[1];
    let tab = ["plan", "usage", "invoices"].includes(wanted) ? wanted : route.path[0] === "usage" || !manage ? "usage" : "plan";
    if (tab === "invoices" && !manage) tab = "usage";
    const data = await loadAll(manage);
    const { billing } = data;
    const info = statusInfo(billing);
    const items = [{ id: "plan", label: "Plan" }, { id: "usage", label: "Usage" }];
    if (data.canManage) items.push({ id: "invoices", label: "Invoices" });
    const state = { plan: billing.plan_id === "trial" ? "starter" : billing.plan_id === "enterprise" ? "team" : billing.plan_id, seats: Math.max(billing.candidate_seats || 0, 1), quote: null, error: "" };
    const chosen = data.plans.find((p) => p.plan_id === state.plan);
    if (chosen) state.seats = Math.min(Math.max(state.seats, billing.seat_usage.candidates || 1, chosen.min_seats || 1), chosen.limits.candidate_seats || state.seats);
    const head = h("div", { class: "page-head" }, h("div", {}, h("h1", { text: data.canManage ? "Plan and billing" : "Plan and usage" }), h("p", { class: "muted", text: data.canManage ? "Choose a plan, change seats, see usage and invoices." : "What the organization uses against its plan. Owners and the billing contact change the plan." })));
    const body = h("div", {});
    stage.replaceChildren(head, banner(info, billing), ui.tabs(items, tab, (id) => Aira.router.go([route.path[0] === "usage" ? "usage" : "billing", id])), body);
    if (route.query.checkout === "success") await afterCheckout(stage, billing);
    if (route.query.checkout === "cancelled") stage.insertBefore(h("div", { class: "cb-note warn", role: "status", text: "Checkout was cancelled. Nothing was charged." }), body);
    if (tab === "plan") renderPlan(body, data, state);
    else if (tab === "usage") renderUsage(body, data);
    else renderInvoices(body, data);
  }

  async function afterCheckout(stage, billing) {
    await refreshIdentity();
    const fresh = await Aira.api("/api/org/billing", { silent: true }).catch(() => null);
    const status = fresh && fresh.billing.status;
    stage.insertBefore(h("div", { class: `cb-note ${status === "active" ? "ok" : "warn"}`, role: "status" }, h("strong", { text: status === "active" ? "Payment received. Your plan is active." : "Waiting for the payment confirmation." }), h("span", { text: status === "active" ? " Automation can run within the new limits." : " This page updates when the provider confirms; reload in a moment." })), stage.querySelector(".tabs"));
    history.replaceState(null, "", `#/${Aira.router.parse().path[0]}`);
    if (fresh) Object.assign(billing, fresh.billing);
    Aira.toast(status === "active" ? "Your plan is active." : "Checkout finished; waiting for confirmation.", status === "active" ? "success" : "");
  }

  function banner(info, b) {
    const parts = [h("span", { class: `badge cb-status ${info.level}`, text: `${titleCase(b.plan_name || b.plan_id)} · ${info.label}` }), h("span", { text: info.text })];
    const node = h("div", { class: `cb-note ${info.level}`, role: info.level === "danger" ? "alert" : "status" }, ...parts);
    if ((b.over_limits || []).length) {
      node.append(h("div", { class: "cb-over" }, h("strong", { text: "Over the plan's limits — automation is paused until the organization is within them: " }), h("span", { text: b.over_limits.join(", ") + ". " }), h("a", { href: "#/org/members", text: "Review members" })));
    }
    return node;
  }

  // -- plan tab ---------------------------------------------------------------------------
  function renderPlan(body, data, state) {
    const { billing } = data;
    const plans = [...(data.trial ? [data.trial] : []), ...data.plans];
    const wrap = h("div", { class: "cb-stack" });
    body.replaceChildren(wrap);
    wrap.append(currentPlanCard(data));
    if (billing.pending) wrap.append(pendingCard(billing, data));
    if (data.canManage) wrap.append(pickerCard(data, state));
    wrap.append(comparisonCard(plans, billing));
  }

  function currentPlanCard(data) {
    const b = data.billing;
    const seats = b.seat_usage || {};
    const facts = ui.kv([
      ["Plan", `${b.plan_name}${b.status === "trialing" ? " (trial)" : ""}`],
      ["State", statusInfo(b).label],
      ["Candidate seats", `${seats.candidates || 0} in use${b.status === "trialing" ? "" : ` of ${b.candidate_seats} purchased`}`],
      ["Coach and admin seats", `${seats.staff || 0} in use${b.entitlements && b.entitlements.limits.staff_seats != null ? ` of ${b.entitlements.limits.staff_seats}` : ""}`],
      [b.status === "active" ? "Renews" : b.status === "trialing" ? "Trial ends" : "Period ended", b.period_end ? fmt.date(b.period_end) : "—"],
      ["Days to renewal", b.status === "active" ? String(b.days_to_renewal) : ""],
      ["Billing", b.provider ? (b.provider === "standin" ? "Stand-in provider (no money moves)" : titleCase(b.provider)) : "None yet"],
    ]);
    const actions = h("div", { class: "btn-row" });
    if (data.canManage) {
      if (["past_due", "read_only"].includes(b.status) && b.provider) actions.append(h("button", { type: "button", class: "btn", text: "Update payment method", onclick: (e) => portal(e.currentTarget) }));
      else if (b.provider && b.status === "active") actions.append(h("button", { type: "button", class: "btn secondary", text: "Manage payment method", onclick: (e) => portal(e.currentTarget) }));
      if (b.status === "active" && ((b.entitlements && b.entitlements.features) || []).includes("overage")) actions.append(h("button", { type: "button", class: "btn secondary", text: b.overage_enabled ? "Stop paying for overage" : "Allow overage (pay per use past the limits)", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await Aira.api("/api/org/billing/overage", { method: "POST", body: { enabled: !b.overage_enabled } }); Aira.toast(b.overage_enabled ? "Work stops at the limits again." : "Work continues past the limits and is billed monthly.", "success"); Aira.router.render(); }) }));
      if (b.status === "active" && !b.cancel_at_period_end) actions.append(h("button", { type: "button", class: "btn danger secondary", text: "Cancel subscription", onclick: () => cancelFlow(b) }));
      if (b.status === "active" && b.cancel_at_period_end) actions.append(h("button", { type: "button", class: "btn", text: "Keep my subscription", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await Aira.api("/api/org/billing/reactivate", { method: "POST", body: {} }); Aira.toast("Subscription kept.", "success"); Aira.router.render(); }) }));
      if (["cancelled"].includes(b.status)) actions.append(h("a", { class: "btn secondary", href: "#/org/data", text: "Export data" }));
    }
    return h("section", { class: "card cb-card" }, h("h2", { text: "Current plan" }), facts, actions);
  }

  async function portal(button) {
    await Aira.withBusy(button, async () => { const data = await Aira.api("/api/org/billing/portal", { method: "POST", body: {} }); window.location.href = data.url; });
  }

  function cancelFlow(b) {
    Aira.modal({
      title: "Cancel subscription?",
      body: h("div", {}, h("p", { text: `Service continues until ${fmt.date(b.period_end)}. After that the organization is read-only and data can be exported for 30 days, then it is deleted.` }), h("p", { class: "muted", text: "You can keep the subscription any time before then." })),
      actions: [{ label: "Keep plan", kind: "ghost" }, { label: "Cancel at period end", kind: "danger", onClick: async () => { const r = await Aira.api("/api/org/billing/cancel", { method: "POST", body: {} }); Aira.toast(`Cancels on ${fmt.date(r.effective_at)}.`, "success"); Aira.router.render(); } }],
    });
  }

  function pendingCard(b, data) {
    const pending = b.pending;
    return h("section", { class: "card cb-card cb-pending" }, h("h2", { text: "Change scheduled" }), h("p", { text: `On ${fmt.date(pending.effective_at)} the plan changes to ${titleCase(pending.plan_id)} with ${pending.seats} candidate seats.` }), h("p", { class: "muted", text: "If the organization is over the new limits then, automation pauses until it is within them." }), data.canManage ? h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary", text: "Cancel this change", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await Aira.api("/api/org/billing/cancel-change", { method: "POST", body: {} }); Aira.toast("Scheduled change cancelled.", "success"); Aira.router.render(); }) })) : null);
  }

  // -- picker: plan, seats, live quote, checkout / change ----------------------------------
  function pickerCard(data, state) {
    const { billing } = data;
    const paid = data.plans.filter((p) => !p.contact_sales);
    const active = billing.status === "active";
    const card = h("section", { class: "card cb-card" });
    const planSelect = ui.select(paid.map((p) => [p.plan_id, `${p.name}`]), state.plan, { id: "cbPlan" });
    const seats = h("input", { id: "cbSeats", type: "number", inputmode: "numeric", step: "1" });
    const range = h("input", { type: "range", "aria-label": "Candidate seats", step: "1" });
    const quote = h("div", { class: "cb-quote", "aria-live": "polite" });
    const go = h("button", { type: "button", class: "btn", text: "Continue" });
    const note = h("div", { class: "muted" });
    let timer = 0;

    const plan = () => data.plans.find((p) => p.plan_id === state.plan);
    function bounds() {
      const p = plan();
      const min = Math.max(1, p.min_seats || 1, active ? 1 : (billing.seat_usage || {}).candidates || 1);
      const max = p.limits.candidate_seats || 10000;
      return [min, max];
    }
    function sync(fromRange) {
      const [min, max] = bounds();
      let value = Number(fromRange ? range.value : seats.value) || min;
      value = Math.max(min, Math.min(max, Math.round(value)));
      state.seats = value;
      seats.min = range.min = String(min);
      seats.max = range.max = String(max);
      seats.value = range.value = String(value);
      schedule();
    }
    function schedule() { clearTimeout(timer); timer = setTimeout(refreshQuote, 250); renderAction(); }
    async function refreshQuote() {
      try {
        state.quote = await Aira.api("/api/org/billing/quote", { method: "POST", body: { plan: state.plan, seats: state.seats }, silent: true });
        state.error = "";
      } catch (error) { state.quote = null; state.error = error.message; }
      renderQuote();
    }
    function renderQuote() {
      const q = state.quote;
      quote.replaceChildren(state.error ? h("div", { class: "cb-note warn", text: state.error }) : q ? h("div", { class: "cb-quote-body" }, h("div", { class: "cb-total" }, h("strong", { text: money(q.monthly_cents, q.currency) }), h("span", { class: "muted", text: ` per month · ${q.seats} seats × ${money(q.seat_cents_month, q.currency)}` })), q.prorated_charge_cents !== undefined ? h("div", { text: `Charged now if you upgrade: ${money(q.prorated_charge_cents, q.currency)} (the rest of this period, prorated).` }) : null, overageLine(q)) : h("span", { class: "muted", text: "Calculating…" }));
    }
    function overageLine(q) {
      const text = overageCell({ plan_id: q.plan_id, overage: q.overage, currency: q.currency });
      return q.overage && Object.keys(q.overage).length ? h("div", { class: "muted", text: `Overage if enabled: ${text}.` }) : null;
    }
    function renderAction() {
      const dir = active ? direction(billing.plan_id, billing.candidate_seats, state.plan, state.seats) : "checkout";
      go.disabled = dir === "same" || !!state.error;
      go.textContent = { checkout: `Continue to checkout (${titleCase(state.plan)})`, up: "Upgrade now", down: "Schedule the change for period end", same: "This is your current plan" }[dir];
      go.className = dir === "down" ? "btn secondary" : "btn";
      note.textContent = { checkout: "You pay on the provider's hosted checkout page. Aira never sees card details.", up: "Applies immediately; the charge is prorated.", down: `Applies on ${billing.period_end ? fmt.date(billing.period_end) : "the period end"}; nothing changes until then.`, same: "" }[dir];
    }
    go.addEventListener("click", (event) => confirmAction(data, state, active, event.currentTarget));
    planSelect.addEventListener("change", () => { state.plan = planSelect.value; sync(false); });
    seats.addEventListener("input", () => sync(false));
    range.addEventListener("input", () => sync(true));

    card.append(h("h2", { text: active ? "Change plan or seats" : "Choose a plan" }), h("p", { class: "muted", text: active ? "Upgrades and added seats apply now with a prorated charge. Downgrades and fewer seats apply at the end of the period." : "Pay once for the seats you need; seats can change later." }),
      h("div", { class: "cb-picker" }, ui.field("Plan", planSelect), ui.field("Candidate seats", seats), h("div", { class: "field cb-range" }, range)), quote, h("div", { class: "btn-row" }, go, h("button", { type: "button", class: "btn ghost", text: "Enterprise: contact sales", onclick: enterpriseInfo })), note);
    if (!paid.some((p) => p.plan_id === state.plan)) state.plan = paid[0].plan_id;
    planSelect.value = state.plan;
    sync(false);
    return card;
  }

  function enterpriseInfo() {
    Aira.modal({ title: "Enterprise", body: h("div", {}, h("p", { text: "Enterprise is arranged with our sales team: custom limits, SSO with SCIM, a named support contact and invoicing instead of card payment." }), h("p", { class: "muted", text: "After the contract is signed, a platform operator activates the plan on this organization; no checkout is needed." })), actions: [{ label: "Close", kind: "ghost" }] });
  }

  function confirmAction(data, state, active, button) {
    const { billing } = data;
    const plan = data.plans.find((p) => p.plan_id === state.plan);
    const q = state.quote;
    if (!active) {
      return Aira.withBusy(button, async () => {
        const out = await Aira.api("/api/org/billing/checkout", { method: "POST", body: { plan: state.plan, seats: state.seats } });
        window.location.href = out.checkout_url;
      });
    }
    const dir = direction(billing.plan_id, billing.candidate_seats, state.plan, state.seats);
    const exceed = dir === "down" ? exceedPreview(billing, plan, state.seats) : [];
    const lines = [h("p", { text: `${titleCase(plan.name)}, ${state.seats} candidate seats, ${q ? money(q.monthly_cents, q.currency) : ""} per month.` })];
    if (dir === "up") lines.push(h("p", { text: `You will be charged ${q ? money(q.prorated_charge_cents || 0, q.currency) : "a prorated amount"} now for the rest of this period. The new limits apply immediately.` }));
    if (dir === "down") {
      lines.push(h("p", { text: `This takes effect on ${fmt.date(billing.period_end)}. Until then nothing changes.` }));
      if (exceed.length) lines.push(h("div", { class: "cb-note warn" }, h("strong", { text: "The organization would be over the new limits: " }), h("span", { text: exceed.join("; ") + ". Automation pauses at that point until it is within them. Remove members before then to avoid it." })));
      else lines.push(h("p", { class: "muted", text: "The organization is within the new plan's seat limits. Search plans per candidate are checked when the change applies." }));
    }
    Aira.modal({
      title: dir === "up" ? "Upgrade now?" : "Schedule this change?",
      body: h("div", {}, ...lines),
      actions: [{ label: "Not now", kind: "ghost" }, { label: dir === "up" ? "Upgrade" : "Schedule change", kind: "", onClick: async () => {
        const result = await Aira.api("/api/org/billing/change-plan", { method: "POST", body: { plan: state.plan, seats: state.seats } });
        if (result.scheduled) Aira.toast(`Scheduled for ${fmt.date(result.effective_at)}.${(result.would_exceed || []).length ? " Over limits then: " + result.would_exceed.join(", ") + "." : ""}`, "success", { timeout: 9000 });
        else Aira.toast(`Upgraded${result.prorated_charge_cents ? `; charged ${money(result.prorated_charge_cents, "usd")}` : ""}.`, "success");
        await refreshIdentity();
        Aira.router.render();
      } }],
    });
  }

  // -- comparison -----------------------------------------------------------------------------
  function comparisonCard(plans, billing) {
    const head = h("tr", {}, h("th", { scope: "col", text: "" }), ...plans.map((p) => h("th", { scope: "col", class: p.plan_id === billing.plan_id ? "current" : "" }, h("div", { text: p.name }), p.plan_id === billing.plan_id ? h("span", { class: "badge", text: "Your plan" }) : null)));
    const rows = [
      h("tr", {}, h("th", { scope: "row", text: "Price" }), ...plans.map((p) => h("td", { text: priceCell(p) }))),
      ...LIMIT_ROWS.map(([label, fn]) => h("tr", {}, h("th", { scope: "row", text: label }), ...plans.map((p) => h("td", { text: safe(fn, p) })))),
      h("tr", {}, h("th", { scope: "row", text: "Overage" }), ...plans.map((p) => h("td", { text: p.plan_id === "trial" ? "Stops at the limit" : overageCell(p) }))),
    ];
    return h("section", { class: "card cb-card" }, h("h2", { text: "Compare plans" }), h("div", { class: "table-wrap cb-compare" }, h("table", { class: "table cb-table" }, h("thead", {}, head), h("tbody", {}, ...rows))), h("p", { class: "muted", text: "Limits are enforced by the server on every request and in every background job; this table is what the plan allows." }));
  }
  const safe = (fn, plan) => { try { return String(fn(plan)); } catch { return "—"; } };

  // -- usage tab -------------------------------------------------------------------------------
  function renderUsage(body, data) {
    const u = data.usage;
    if (!u) { body.replaceChildren(ui.empty("Usage is not available to you", "Ask an owner or administrator.")); return; }
    const b = data.billing;
    const wrap = h("div", { class: "cb-stack" });
    wrap.append(h("section", { class: "card cb-card" }, h("h2", { text: `Usage in ${u.period}` }), h("p", { class: "muted", text: `Counted per calendar month in UTC; resets on the 1st. ${b.status === "active" ? `The plan renews in ${b.days_to_renewal} day${b.days_to_renewal === 1 ? "" : "s"}.` : ""}` }),
      h("div", { class: "cb-meters" },
        meter("Candidate seats", u.seats.candidates.used, u.seats.candidates.limit, { link: "#/org/members" }),
        meter("Coach and admin seats", u.seats.staff.used, u.seats.staff.limit, { link: "#/org/members" }),
        meter("Applications this month", u.applications.used, u.applications.limit, { link: u.applications.link, note: u.applications.overage ? `${num(u.applications.overage)} over the limit (metered overage)` : "" }),
        meter("Searches", u.searches.used, null),
        meter("API searches", u.api_searches.used, u.api_searches.limit),
        meter("AI cover letters", u.cover_letters.used, u.cover_letters.limit),
        meter("Document storage", u.storage.used_bytes, u.storage.limit_bytes, { unit: "bytes" }))));
    const cost = u.cost_per_application || {};
    wrap.append(h("section", { class: "card cb-card" }, h("h2", { text: "What an application costs us" }), h("p", { class: "muted", text: "Browser time and model calls per submitted application this month; these drive overage pricing." }), ui.kv([["Browser seconds per application", cost.browser_seconds == null ? "—" : String(cost.browser_seconds)], ["Model calls per application", cost.model_calls == null ? "—" : String(cost.model_calls)], ["Searches per application", cost.searches == null ? "—" : String(cost.searches)], ["Browser seconds this month", num(u.browser_seconds || 0)], ["Model calls this month", num(u.model_calls || 0)]])));
    const history = (u.history || []).slice().reverse();
    const maxApps = Math.max(1, ...history.map((r) => r.applications || 0));
    wrap.append(h("section", { class: "card cb-card" }, h("h2", { text: "Last six months" }), history.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, ...["Month", "Applications", "", "Searches", "API searches", "Cover letters", "Model calls", "Overage"].map((t, i) => h("th", { scope: "col", class: i > 0 && i !== 2 ? "num" : "", text: t })))), h("tbody", {}, ...history.map((r) => h("tr", {}, h("td", { text: r.period }), h("td", { class: "num", text: num(r.applications || 0) }), h("td", { class: "cb-spark" }, h("i", { style: { width: `${Math.round((100 * (r.applications || 0)) / maxApps)}%` } })), h("td", { class: "num", text: num(r.searches || 0) }), h("td", { class: "num", text: num(r.api_searches || 0) }), h("td", { class: "num", text: num(r.cover_letters || 0) }), h("td", { class: "num", text: num(r.model_calls || 0) }), h("td", { class: "num", text: num(r["applications:overage"] || 0) })))))) : ui.empty("No usage yet", "Counters appear when candidates search and apply.")));
    body.replaceChildren(wrap);
  }

  // -- invoices ----------------------------------------------------------------------------------
  function renderInvoices(body, data) {
    const rows = data.invoices;
    body.replaceChildren(h("section", { class: "card cb-card" }, h("h2", { text: "Invoices" }), rows.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, ...["Date", "Kind", "Description", "Amount", "Status", "Period", ""].map((t, i) => h("th", { scope: "col", class: i === 3 ? "num" : "", text: t })))), h("tbody", {}, ...rows.map((r) => h("tr", {}, h("td", { text: fmt.date(r.created_at) }), h("td", { text: titleCase(r.kind) }), h("td", { text: (r.lines || []).map((l) => l.description).join("; ") || "—" }), h("td", { class: "num", text: money(r.amount_cents, r.currency) }), h("td", {}, h("span", { class: `badge cb-inv ${r.status}`, text: r.status })), h("td", { text: r.period_start ? `${fmt.date(r.period_start)} – ${r.period_end ? fmt.date(r.period_end) : ""}` : "—" }), h("td", {}, r.hosted_url ? h("a", { href: r.hosted_url, target: "_blank", rel: "noopener", text: "View" }) : null)))))) : ui.empty("No invoices yet", "Invoices appear after the first payment.")));
  }

  // ---------------------------------------------------------------------------
  // Registration
  // ---------------------------------------------------------------------------
  cloud.register({ id: "billing", label: "Plan & billing", icon: "settings", group: "Organization", key: "b", roles: ["owner", "billing"], view });
  cloud.register({ id: "usage", label: "Usage", icon: "analytics", group: "Organization", key: "u", roles: ["admin", "auditor"], view });
  cloud.billingView = view;

  // Core's checkout return address and the dashboard's links use #/org/billing: send them to this page.
  document.addEventListener("DOMContentLoaded", () => {
    const original = Aira.views.org;
    Aira.views.org = async (stage, route) => {
      if (route.path[1] === "billing") {
        Aira.router.go([cloud.can("billing.manage") ? "billing" : "usage", ...(route.path[2] ? [route.path[2]] : [])], route.query, { replace: true });
        return;
      }
      if (original) return original(stage, route);
      stage.replaceChildren(ui.empty("Page not found", "Choose a section from the navigation."));
    };
  });
})();

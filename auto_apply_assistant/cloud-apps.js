/* Aira hosted build — the Apps page (#/org/apps): which apps the organization can open, and the per-app subscriptions.
 *
 * Every app is a card: what it is, what it costs on this plan (per candidate seat, "Included", or not sold on the
 * plan), and whether it is on. Owners and billing contacts turn an app on (a quote first, prorated like a seat
 * change) or off at the period end; administrators and auditors read the same page. The server decides everything
 * (/api/org/apps and /api/org/apps/<id>/{quote,subscribe,unsubscribe,keep}); the page only hides what it would refuse.
 * The sidebar entry ("org-apps", a redirect to #/org/apps) is registered in cloud-org.js with the other admin pages.
 */
"use strict";

(function () {
  const { h } = window;
  const cloud = Aira.cloud;
  if (!cloud || !cloud.admin) return;
  const { fmt, ui } = Aira;
  const A = cloud.admin;

  const titleCase = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "");
  const money = (cents, currency) => (Number(cents || 0) / 100).toLocaleString([], { style: "currency", currency: String(currency || "usd").toUpperCase() });
  const perSeat = (app) => `${money(app.seat_cents_month, app.currency)} per candidate seat per month`;

  // ---------------------------------------------------------------------------
  // Pure helpers (what a card says, from the server's status words)
  // ---------------------------------------------------------------------------
  function priceLine(app) {
    switch (app.status) {
      case "included": return "Included in your plan";
      case "granted": return "Granted to your organization at no charge";
      case "disabled": return "Switched off by Monosage for now";
      case "unavailable": return app.required_plan ? `Not on your plan: upgrade to ${titleCase(app.required_plan)}` : "Not available on your plan";
      default: return perSeat(app);
    }
  }

  function statusBadge(app) {
    const map = {
      included: ["On", "ok"],
      granted: ["On", "ok"],
      active: ["On", "ok"],
      cancelling: ["Turning off", "warn"],
      unavailable: ["Not on your plan", ""],
      off: ["Off", ""],
      disabled: ["Paused", "warn"],
    };
    const [label, kind] = map[app.status] || [titleCase(app.status), ""];
    return h("span", { class: `badge ca-status ${kind}`, text: label });
  }

  function statusText(app, data) {
    if (app.status === "active") return `${money(app.monthly_cents, app.currency)} per month for ${app.seats} seat${app.seats === 1 ? "" : "s"}${data.period_end ? `; renews on ${fmt.date(data.period_end)}` : ""}.`;
    if (app.status === "cancelling") return `Turns off on ${app.effective_until ? fmt.date(app.effective_until) : "the period end"}. It keeps working until then.`;
    if (app.status === "granted") return app.note ? `Note from Monosage: ${app.note}` : "";
    if (app.status === "disabled") return "Nobody can open it until Monosage switches it back on. Your data is kept, and it is not billed meanwhile.";
    if (app.status === "off") return data.status === "active" ? "Turning it on is billed with your plan and prorated for the rest of this period." : "Choose a plan under Plan & billing first; the trial includes every app.";
    return "";
  }

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------
  async function refresh() {
    try {
      cloud.me = await Aira.api("/api/auth/me", { silent: true });
      cloud.buildNav();
      if (Aira.apps && Aira.apps.load) await Aira.apps.load();
    } catch { /* the page reloads its own data next */ }
    Aira.router.render();
  }

  async function subscribe(app, button) {
    let quote;
    try {
      quote = await Aira.withBusy(button, () => Aira.api(`/api/org/apps/${encodeURIComponent(app.id)}/quote`, { method: "POST", body: {} }));
    } catch (error) {
      Aira.toast(error.message, "error");
      return;
    }
    const lines = [
      h("p", { text: `${app.name}: ${money(quote.seat_cents_month, quote.currency)} per candidate seat per month, ${quote.seats} seat${quote.seats === 1 ? "" : "s"} — ${money(quote.monthly_cents, quote.currency)} per month on top of your ${titleCase(quote.plan_id)} plan.` }),
      h("p", { text: `You will be charged ${money(quote.prorated_charge_cents, quote.currency)} now for the rest of this period${quote.period_end ? ` (until ${fmt.date(quote.period_end)})` : ""}. After that it renews with the plan.` }),
      h("p", { class: "muted", text: "Seat changes re-price the app the same way they re-price the plan. You can turn it off at any time; it then stops at the period end." }),
    ];
    Aira.modal({
      title: `Turn on ${app.name}?`,
      body: h("div", {}, ...lines),
      actions: [{ label: "Not now", kind: "ghost" }, { label: "Subscribe", kind: "", onClick: async () => {
        const result = await Aira.api(`/api/org/apps/${encodeURIComponent(app.id)}/subscribe`, { method: "POST", body: {} });
        Aira.toast(`${app.name} is on${result.prorated_charge_cents ? `; charged ${money(result.prorated_charge_cents, quote.currency)}` : ""}.`, "success");
        await refresh();
      } }],
    });
  }

  function unsubscribe(app, data) {
    Aira.modal({
      title: `Turn off ${app.name}?`,
      body: h("div", {}, h("p", { text: `${app.name} keeps working until ${data.period_end ? fmt.date(data.period_end) : "the end of this period"} and is not billed after that.` }), h("p", { class: "muted", text: "Nothing is deleted. You can keep it until then, or turn it on again later." })),
      actions: [{ label: "Keep it", kind: "ghost" }, { label: "Turn off at period end", kind: "danger", onClick: async () => {
        await Aira.api(`/api/org/apps/${encodeURIComponent(app.id)}/unsubscribe`, { method: "POST", body: {} });
        Aira.toast(`${app.name} turns off at the period end.`, "success");
        await refresh();
      } }],
    });
  }

  async function keep(app, button) {
    await Aira.withBusy(button, async () => {
      await Aira.api(`/api/org/apps/${encodeURIComponent(app.id)}/keep`, { method: "POST", body: {} });
      Aira.toast(`${app.name} stays on.`, "success");
      await refresh();
    });
  }

  // ---------------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------------
  function actionsFor(app, data) {
    const row = h("div", { class: "btn-row" });
    if (!data.can_manage) {
      if (app.status === "unavailable" || app.status === "off") row.append(h("span", { class: "muted", text: "An owner or billing contact can change this." }));
      return row;
    }
    if (app.status === "off" && data.status === "active") row.append(h("button", { type: "button", class: "btn", "data-action": "subscribe", "data-app": app.id, text: `Subscribe · ${money(app.seat_cents_month, app.currency)} per seat`, onclick: (e) => subscribe(app, e.currentTarget) }));
    else if (app.status === "off") row.append(h("a", { class: "btn secondary", href: "#/billing", text: "Choose a plan" }));
    else if (app.status === "active") row.append(h("button", { type: "button", class: "btn secondary", "data-action": "unsubscribe", "data-app": app.id, text: "Cancel at period end", onclick: () => unsubscribe(app, data) }));
    else if (app.status === "cancelling") row.append(h("button", { type: "button", class: "btn", "data-action": "keep", "data-app": app.id, text: "Keep", onclick: (e) => keep(app, e.currentTarget) }));
    else if (app.status === "unavailable") row.append(h("a", { class: "btn secondary", href: "#/billing", text: app.required_plan ? `Upgrade to ${titleCase(app.required_plan)}` : "See plans" }));
    return row;
  }

  function card(app, data) {
    return h("section", { class: "card cb-card ca-app", "data-app": app.id, "data-status": app.status },
      h("div", { class: "ca-app-head" }, h("h2", { text: app.name }), statusBadge(app)),
      h("p", { class: "muted", text: app.tagline || "" }),
      h("p", { text: app.description || "" }),
      app.industry ? h("p", { class: "muted small", text: `For ${app.industry.toLowerCase()}` }) : null,
      (app.highlights || []).length ? h("ul", { class: "ca-highlights" }, ...app.highlights.map((item) => h("li", { text: item }))) : null,
      h("div", { class: "ca-price" }, h("strong", { text: priceLine(app) })),
      statusText(app, data) ? h("p", { class: "muted ca-status-text", text: statusText(app, data) }) : null,
      actionsFor(app, data));
  }

  function totals(data) {
    const plan = Number(data.monthly_cents || 0) - Number(data.addons_monthly_cents || 0);
    const billingHref = cloud.role() === "owner" || cloud.role() === "billing" ? "#/billing" : "#/usage";
    const rows = [[`${titleCase(data.plan_name || data.plan_id)} plan, ${data.seats} candidate seat${data.seats === 1 ? "" : "s"}`, data.status === "active" ? money(plan, data.currency) : titleCase(String(data.status || "").replace("_", " "))]];
    for (const app of data.apps.filter((a) => a.status === "active" || a.status === "cancelling")) rows.push([`${app.name} app${app.status === "cancelling" ? " (until the period end)" : ""}`, money(app.monthly_cents, app.currency)]);
    rows.push(["Total per month", data.status === "active" ? money(data.monthly_cents, data.currency) : "—"]);
    return h("section", { class: "card cb-card ca-totals" },
      h("h2", { text: "Your monthly total" }),
      ui.kv(rows.map(([k, v]) => [k, h("strong", { text: v })])),
      h("p", { class: "muted", text: data.period_end && data.status === "active" ? `Renews on ${fmt.date(data.period_end)}. Apps are billed with the plan, on the same invoice.` : "Apps are billed with the plan, on the same invoice." }),
      h("div", { class: "btn-row" }, h("a", { class: "btn secondary", href: billingHref, text: "Plan & billing" })));
  }

  A.pages.apps = async (stage) => {
    A.crumbs("Apps");
    stage.replaceChildren(ui.skeleton(3));
    let data;
    try {
      data = await Aira.api("/api/org/apps", { silent: true });
    } catch (error) {
      stage.replaceChildren(ui.error(error, () => Aira.router.render()));
      return;
    }
    const page = h("div", { class: "adm-page ca-apps" });
    page.append(A.head("Apps", data.can_manage
      ? "The apps your organization can open. Turn one on and it is billed with your plan, per candidate seat; turn it off and it stops at the period end."
      : "The apps your organization can open. Owners and billing contacts turn them on and off under Plan & billing."));
    const core = data.apps.filter((app) => app.category !== "industry" && app.category !== "life");
    const everyday = data.apps.filter((app) => app.category === "life");
    const industry = data.apps.filter((app) => app.category === "industry");
    page.append(h("div", { class: "ca-grid" }, ...core.map((app) => card(app, data))));
    if (everyday.length) {
      page.append(h("h2", { class: "ca-group", text: "Everyday apps" }), h("p", { class: "muted", text: "For every member, every day: family chat and photos, medicine reminders, learning, games and the household planner. Each person's data is their own." }));
      page.append(h("div", { class: "ca-grid" }, ...everyday.map((app) => card(app, data))));
    }
    if (industry.length) {
      page.append(h("h2", { class: "ca-group", text: "Business apps" }), h("p", { class: "muted", text: "Sales, Marketing with Leads, Service, Commerce, and Courses & Events: they share your organization's customers, revenue and email in the Business hub." }));
      page.append(h("div", { class: "ca-grid" }, ...industry.map((app) => card(app, data))));
    }
    page.append(totals(data));
    stage.replaceChildren(page);
  };

  cloud.appsPage = { priceLine, statusText };
})();

/* Aira — Dashboard widgets: API usage and Careers shortcuts.
   The Dashboard view calls Aira.dashboardWidgets() and appends the nodes it returns. Both widgets fetch on their own
   after the Dashboard has drawn, so a slow or failing SerpApi never holds the rest of the page back. */
"use strict";
(() => {
  const { h, api, fmt, ui } = Aira;

  const num = (value) => Number(value || 0).toLocaleString();
  const KIND = {
    "company-intel": { tab: "company", label: "Company" },
    salary: { tab: "salary", label: "Salary" },
    trends: { tab: "market", label: "Market" },
    "interview-prep": { tab: "interview", label: "Interview prep" },
    travel: { tab: "travel", label: "Travel" },
    learn: { tab: "learn", label: "Learn" },
  };
  const apiLink = (tab, query = {}) => `#/api?${new URLSearchParams({ tab, ...query })}`;
  const careersLink = (tab, query = {}) => `#/careers?${new URLSearchParams({ tab, ...query })}`;

  function panelHead(title, right) {
    return h("div", { class: "panel-head" }, h("h3", { text: title }), right || null);
  }

  // A failed widget says so and can retry; it never throws into the Dashboard.
  function failed(node, title, error, reload) {
    node.replaceChildren(panelHead(title), h("div", { class: "dw-body" }, h("p", { class: "muted", role: "alert", text: error && error.message ? error.message : "This widget could not load." }), h("button", { type: "button", class: "btn secondary sm", text: "Try again", onclick: reload })));
    node.removeAttribute("aria-busy");
  }

  // Last 14 days of searches as small bars. The label carries the numbers; the bars are decoration.
  function spark(byDay) {
    const days = (byDay || []).slice(-14);
    const peak = Math.max(1, ...days.map((day) => day.searches || 0));
    const total = days.reduce((sum, day) => sum + (day.searches || 0), 0);
    return h("div", { class: "dw-spark", role: "img", "aria-label": `Searches per day over the last ${days.length} days, ${num(total)} in total, busiest day ${num(peak === 1 && !total ? 0 : peak)}` },
      ...days.map((day) => h("i", { class: day.searches ? "" : "idle", style: { height: `${Math.max(6, Math.round(((day.searches || 0) / peak) * 100))}%` }, title: `${fmt.date(day.day)}: ${num(day.searches)} searches` })));
  }

  function mini(label, value, sub, href) {
    const children = [h("span", { class: "label", text: label }), h("span", { class: "value", text: value }), sub ? h("span", { class: "sub", text: sub }) : null];
    return href ? h("a", { class: "dw-stat", href }, ...children) : h("div", { class: "dw-stat" }, ...children);
  }

  // -------------------------------------------------------------------------
  // API
  // -------------------------------------------------------------------------
  function fillApi(node, data) {
    const title = "API";
    const open = h("a", { class: "btn ghost sm", href: apiLink("overview"), text: "Open console" });
    if (!data.configured) {
      node.replaceChildren(panelHead(title, open), h("div", { class: "dw-body" },
        h("div", { class: "dw-setup" },
          h("strong", { text: "Connect a SerpApi key" }),
          h("p", { class: "muted", text: "One key powers Google Jobs in Jobs, the Careers workspace and a cached, SerpApi-compatible endpoint for your own scripts. It is stored encrypted on this computer." }),
          h("div", { class: "btn-row" }, h("a", { class: "btn sm", href: apiLink("account"), text: "Connect key" }), h("a", { class: "btn secondary sm", href: apiLink("engines"), text: "Browse engines" })))));
      node.removeAttribute("aria-busy");
      return;
    }
    const usage = data.usage || {};
    const account = data.account;
    const left = account ? account.total_searches_left : null;
    const monthly = account ? account.searches_per_month : null;
    const low = account && monthly ? account.plan_searches_left / monthly < 0.1 && !account.extra_credits : false;
    const hitRate = usage.last_30_days ? fmt.pct((usage.cache_hit_rate || 0) * 100) : "—";
    const source = data.key && data.key.source === "env" ? "Environment key" : `Key ending ${(data.key && data.key.last4) || "····"}`;
    const monitors = data.monitors || {};
    node.replaceChildren(
      panelHead(title, h("div", { class: "btn-row" }, h("span", { class: "badge", text: source }), open)),
      h("div", { class: "dw-body" },
        h("div", { class: "dw-stats" },
          mini("Today", num(usage.today), "searches", apiLink("history")),
          mini("30 days", num(usage.last_30_days), `${num(usage.credits_used_30d)} credits used`, apiLink("history")),
          mini("Cache hits", hitRate, "cost nothing", apiLink("overview")),
          mini("Searches left", left == null ? "—" : num(left), account ? `${account.plan_name || "Plan"}${monthly ? ` · ${num(monthly)} a month` : ""}` : "plan not readable", apiLink("account"))),
        spark(usage.by_day),
        h("div", { class: "dw-caption muted" }, h("span", { text: "Last 14 days" }), h("span", { text: monitors.active ? `${fmt.plural(monitors.active, "monitor")} running` : "No monitors running" })),
        data.account_error ? h("p", { class: "note warn", role: "status" }, "SerpApi usage could not be read: ", data.account_error, " ", h("a", { href: apiLink("account"), text: "Check the key" }), ".") : null,
        low ? h("p", { class: "note warn", role: "status" }, "Fewer than 10% of this month's searches remain. Cached results and monitors with long intervals stretch them. ", h("a", { href: apiLink("account"), text: "See the plan" }), ".") : null,
        h("div", { class: "btn-row" }, h("a", { class: "btn secondary sm", href: apiLink("playground"), text: "Playground" }), h("a", { class: "btn ghost sm", href: apiLink("history"), text: "History" }), h("a", { class: "btn ghost sm", href: apiLink("keys"), text: "API keys" }))));
    node.removeAttribute("aria-busy");
  }

  // -------------------------------------------------------------------------
  // Careers
  // -------------------------------------------------------------------------
  function fillCareers(node, data) {
    const alerts = data.alerts || [];
    const saved = data.saved_searches || [];
    const reports = data.recent_intel || [];
    const fresh = alerts.reduce((sum, alert) => sum + (alert.new_count || 0), 0);
    Aira.renderNav({ careers: fresh });

    const profile = (Aira.state.profile && Aira.state.profile.candidate_profile) || {};
    const input = h("input", { type: "search", id: "dwCareersQ", maxlength: 200, placeholder: profile.current_title ? `e.g. ${profile.current_title}` : "Role or keywords", "aria-label": "Search Google Jobs for a role" });
    const form = h("form", { class: "dw-search", role: "search", onsubmit: (event) => { event.preventDefault(); Aira.router.go(["careers"], { tab: "jobs", q: input.value.trim() }); } },
      input, h("button", { type: "submit", class: "btn sm", text: "Search jobs" }));

    const rows = [
      ...alerts.slice(0, 3).map((alert) => h("a", { class: "row", href: alert.new_count ? careersLink("alerts", { alert: alert.id }) : careersLink("alerts") },
        h("div", {}, h("span", { class: "title", text: alert.name || alert.q || "Job alert" }), h("span", { class: "sub", text: ["Alert", alert.location, alert.enabled ? (alert.last_run_at ? `checked ${fmt.ago(alert.last_run_at)}` : "waiting for its first run") : "paused"].filter(Boolean).join(" · ") })),
        alert.new_count ? h("span", { class: "badge stage applied", text: `${alert.new_count} new` }) : null)),
      ...saved.slice(0, 3).map((item) => h("a", { class: "row", href: careersLink("jobs", { q: item.params.q || "", location: item.params.location || "" }) },
        h("div", {}, h("span", { class: "title", text: item.name || item.params.q || "Saved search" }), h("span", { class: "sub", text: ["Saved search", item.params.location, item.last_run_at ? `last run ${fmt.ago(item.last_run_at)}` : ""].filter(Boolean).join(" · ") })),
        h("span", { class: "badge", text: "Open" }))),
      ...reports.slice(0, 2).map((report) => {
        const kind = KIND[report.kind] || { tab: "company", label: "Report" };
        return h("a", { class: "row", href: careersLink(kind.tab) },
          h("div", {}, h("span", { class: "title", text: report.title || kind.label }), h("span", { class: "sub", text: `${kind.label} report · ${fmt.ago(report.generated_at)}${report.errors ? ` · ${fmt.plural(report.errors, "section")} failed` : ""}` })),
          h("span", { class: "badge", text: kind.label }));
      }),
    ];
    node.replaceChildren(
      panelHead("Careers", fresh ? h("a", { class: "badge stage applied", href: careersLink("alerts"), text: `${fmt.plural(fresh, "new job")} from alerts` }) : h("a", { class: "btn ghost sm", href: careersLink("jobs"), text: "Open Careers" })),
      h("div", { class: "dw-body" },
        form,
        !data.configured ? h("p", { class: "note warn", role: "status" }, "Google Jobs and the research tabs need a SerpApi key. ", h("a", { href: apiLink("account"), text: "Connect one in API" }), ".") : null),
      rows.length ? h("div", { class: "list" }, ...rows) : h("div", { class: "empty dw-empty" }, h("strong", { text: "No searches or alerts yet" }), h("div", { text: "Search for a role, then save the search or add an alert to hear about new postings." })),
      h("div", { class: "dw-body dw-foot" }, h("div", { class: "btn-row" },
        h("a", { class: "btn secondary sm", href: careersLink("company"), text: "Research a company" }),
        h("a", { class: "btn ghost sm", href: careersLink("salary"), text: "Check pay" }),
        h("a", { class: "btn ghost sm", href: careersLink("interview"), text: "Interview prep" }),
        h("a", { class: "btn ghost sm", href: careersLink("alerts"), text: "Alerts" }))));
    node.removeAttribute("aria-busy");
  }

  Aira.dashboardWidgets = () => {
    const apiNode = h("section", { class: "panel dw-panel", "aria-label": "API usage", "aria-busy": "true" });
    const careersNode = h("section", { class: "panel dw-panel", "aria-label": "Careers", "aria-busy": "true" });
    const loading = (node, title) => node.replaceChildren(panelHead(title), h("div", { class: "dw-body" }, ui.skeleton(3)));
    const loadApi = () => { loading(apiNode, "API"); return api("/api/airaapi/overview", { silent: true }).then((data) => fillApi(apiNode, data), (error) => failed(apiNode, "API", error, loadApi)); };
    const loadCareers = () => { loading(careersNode, "Careers"); return api("/api/careers/overview", { silent: true }).then((data) => fillCareers(careersNode, data), (error) => failed(careersNode, "Careers", error, loadCareers)); };
    loadApi();
    loadCareers();
    return [apiNode, careersNode];
  };
})();

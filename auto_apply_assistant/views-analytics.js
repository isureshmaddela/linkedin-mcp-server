/* Aira — Analytics */
"use strict";
(() => {
  const { h, api, fmt, ui, router } = Aira;
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = (tag, attrs = {}, ...children) => { const node = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v)); for (const child of children) node.append(child); return node; };

  Aira.views.analytics = async (main, route) => {
    const days = route.query.days || Aira.recall("analytics.days", "30");
    const source = route.query.source || "";
    const stage = route.query.stage || "";
    Aira.remember("analytics.days", days);
    const content = h("div", {});
    const daysSelect = ui.select([["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"], ["365", "Last year"]], days);
    const sourceSelect = ui.select([["", "All sources"]], source);
    const stageSelect = ui.select([["", "Any status"]], stage);
    const apply = () => router.go(["analytics"], { days: daysSelect.value, source: sourceSelect.value, stage: stageSelect.value }, { replace: true });
    for (const control of [daysSelect, sourceSelect, stageSelect]) control.addEventListener("change", apply);
    main.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Analytics" }), h("p", { text: "Honest numbers: rates show n/a when there is nothing to divide by. Click a metric to see the records behind it." }))),
      h("div", { class: "filters careers-compact-filters" }, ui.field("Period", daysSelect), ui.field("Source", sourceSelect), ui.field("Status", stageSelect)),
      content,
    );
    const load = async () => {
      content.replaceChildren(ui.skeleton(5));
      try {
        const data = await api(`/api/analytics?${new URLSearchParams({ days, source, stage })}`);
        if (sourceSelect.options.length === 1) for (const item of data.by_source) sourceSelect.append(h("option", { value: item.source, text: item.source === "manual" ? "Added by you" : fmt.title(item.source.replaceAll("_", " ")), selected: item.source === source }));
        if (stageSelect.options.length === 1) for (const item of data.pipeline) stageSelect.append(h("option", { value: item.stage, text: fmt.title(fmt.stage(item.stage)), selected: item.stage === stage }));
        render(content, data);
      } catch (error) { content.replaceChildren(ui.error(error, load)); }
    };
    await load();
  };

  function render(content, data) {
    const m = data.metrics;
    const def = (key) => data.definitions[key] || "";
    const tile = (label, value, key, href, sub) => h("a", { class: "stat", href: href || "#/applications", title: def(key) }, h("span", { class: "label", text: label }), h("span", { class: "value", text: value === null || value === undefined ? "n/a" : typeof value === "number" && key.endsWith("rate") ? `${value}%` : String(value) }), h("span", { class: "sub", text: sub || def(key).split(".")[0] }));
    const tiles = h("div", { class: "stats", style: { marginBottom: "16px" } },
      tile("Jobs discovered", m.jobs_discovered, "jobs_discovered", "#/jobs/all"),
      tile("Jobs saved", m.jobs_saved, "jobs_saved", "#/jobs/saved"),
      tile("Prepared", m.applications_prepared, "applications_prepared", "#/applications?tab=queue"),
      tile("Submitted", m.applications_submitted, "applications_submitted", "#/jobs/all?stage=applied"),
      tile("Response rate", m.response_rate, "response_rate", "#/applications", m.response_rate === null ? "No submitted applications yet" : undefined),
      tile("Interview rate", m.interview_rate, "interview_rate", "#/interviews", m.interview_rate === null ? "No submitted applications yet" : undefined),
      tile("Offer rate", m.offer_rate, "offer_rate", "#/applications", m.offer_rate === null ? "No submitted applications yet" : undefined),
      tile("Automation success", m.automation_success_rate, "automation_success_rate", "#/settings/automation", m.automation_runs ? `${m.automation_runs} runs · ${m.automation_failures} failed` : "No scheduled runs in period"),
      tile("Recruiter activity", m.recruiter_interactions, "recruiter_interactions", "#/recruiters", `${fmt.plural(m.recruiters, "contact")}`),
      tile("Interviews booked", m.interviews_scheduled, "interviews_scheduled", "#/interviews", m.interviews_upcoming ? `${m.interviews_upcoming} coming up` : "Booked in the selected period"),
    );
    const pipelineRows = data.pipeline.filter((p) => p.count > 0);
    const maxCount = Math.max(1, ...pipelineRows.map((p) => p.count));
    const pipeline = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Current pipeline" }), h("small", { class: "muted", text: "jobs per status" })), h("div", { class: "panel-body" }, pipelineRows.length ? h("div", { class: "bars" }, ...pipelineRows.map((p) => h("a", { class: "bar-row", href: `#/jobs/all?stage=${p.stage}`, title: `${p.count} in ${fmt.stage(p.stage)}` }, h("span", { text: fmt.title(fmt.stage(p.stage)) }), h("div", { class: "track" }, h("i", { style: { width: `${Math.max(2, (100 * p.count) / maxCount)}%` } })), h("span", { class: "n", text: String(p.count) })))) : h("div", { class: "empty", text: "No jobs yet." })));
    const timeline = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Activity over time" }), h("small", { class: "muted", text: "events per day" })), h("div", { class: "panel-body" }, lineChart(data.timeline)));
    const sourcesTable = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Source conversion" }), h("small", { class: "muted", title: def("source_conversion"), text: "submitted ÷ discovered" })), data.by_source.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Source" }), h("th", { class: "num", text: "Discovered" }), h("th", { class: "num", text: "Submitted" }), h("th", { class: "num", text: "Interviewing" }), h("th", { class: "num", text: "Conversion" }))), h("tbody", {}, ...data.by_source.map((s) => h("tr", {}, h("td", {}, h("a", { href: `#/jobs/all?source=${s.source}`, text: s.source === "manual" ? "Added by you" : fmt.title(s.source.replaceAll("_", " ")) })), h("td", { class: "num", text: String(s.discovered) }), h("td", { class: "num", text: String(s.submitted) }), h("td", { class: "num", text: String(s.interviewing) }), h("td", { class: "num", text: fmt.pct(s.conversion) })))))) : h("div", { class: "empty", text: "No sourced jobs yet." }));
    const companies = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Top companies" })), data.top_companies.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Company" }), h("th", { class: "num", text: "Jobs" }), h("th", { class: "num", text: "Applications" }))), h("tbody", {}, ...data.top_companies.map((c) => h("tr", {}, h("td", {}, h("a", { href: `#/jobs/all?q=${encodeURIComponent(c.company)}`, text: c.company })), h("td", { class: "num", text: String(c.jobs) }), h("td", { class: "num", text: String(c.applications || 0) })))))) : h("div", { class: "empty", text: "No companies yet." }));
    const resumes = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Resume versions" }), h("small", { class: "muted", text: "interview rate per version used" })), data.resume_versions.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Version" }), h("th", { class: "num", text: "Applications" }), h("th", { class: "num", text: "Interviews" }), h("th", { class: "num", text: "Rate" }))), h("tbody", {}, ...data.resume_versions.map((r) => h("tr", {}, h("td", { text: r.resume_hash === "unversioned" ? "No tailored resume" : r.resume_hash.slice(0, 10) }), h("td", { class: "num", text: String(r.applications) }), h("td", { class: "num", text: String(r.interviews || 0) }), h("td", { class: "num", text: fmt.pct(r.interview_rate) })))))) : h("div", { class: "empty", text: "Submit applications with tailored resumes to compare versions." }));
    const letters = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Cover letter performance" })), h("div", { class: "panel-body" }, ui.kv([["With cover letter", `${data.cover_letters.with_letter} applications · response ${fmt.pct(data.cover_letters.with_letter_response_rate)}`], ["Without cover letter", `${data.cover_letters.without_letter} applications · response ${fmt.pct(data.cover_letters.without_letter_response_rate)}`]]), h("p", { class: "muted", style: { fontSize: "12px", marginTop: "8px" }, text: "Rates need at least a handful of applications in each group to mean anything." })));
    const definitions = h("details", { class: "card", style: { marginTop: "16px" } }, h("summary", { text: "Metric definitions" }), h("dl", { class: "kv", style: { marginTop: "10px" } }, ...Object.entries(data.definitions).flatMap(([k, v]) => [h("dt", { text: fmt.title(fmt.stage(k)) }), h("dd", { text: v })])));
    content.replaceChildren(tiles, h("div", { class: "grid cols-2" }, pipeline, timeline, sourcesTable, companies, resumes, letters), definitions);
  }

  function lineChart(points) {
    if (!points || !points.length) return h("div", { class: "empty", text: "No events in this period." });
    const series = [["discovered", "Discovered", "var(--series-1)"], ["applied", "Applied", "var(--series-2)"], ["interviews", "Interviews", "var(--series-3)"]];
    const width = 640, height = 220, padL = 36, padR = 12, padT = 12, padB = 28;
    const maxY = Math.max(1, ...points.flatMap((p) => series.map(([key]) => p[key] || 0)));
    const x = (i) => padL + (i * (width - padL - padR)) / Math.max(1, points.length - 1);
    const y = (v) => padT + (height - padT - padB) * (1 - v / maxY);
    const chart = svg("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Daily counts of discovered jobs, applications, and interviews" });
    const ticks = 4;
    for (let t = 0; t <= ticks; t += 1) { const value = (maxY * t) / ticks; chart.append(svg("line", { class: "grid-line", x1: padL, x2: width - padR, y1: y(value), y2: y(value) }), svg("text", { x: padL - 6, y: y(value) + 4, "text-anchor": "end" }, document.createTextNode(String(Math.round(value))))); }
    const labelEvery = Math.max(1, Math.ceil(points.length / 6));
    points.forEach((p, i) => { if (i % labelEvery === 0 || i === points.length - 1) chart.append(svg("text", { x: x(i), y: height - 8, "text-anchor": "middle" }, document.createTextNode(p.day.slice(5)))); });
    for (const [key, , color] of series) {
      const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[key] || 0).toFixed(1)}`).join(" ");
      chart.append(svg("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    }
    const hover = svg("line", { class: "axis", x1: 0, x2: 0, y1: padT, y2: height - padB, style: "display:none" });
    const dots = series.map(([, , color]) => svg("circle", { r: 4, fill: color, stroke: "var(--surface)", "stroke-width": 2, style: "display:none" }));
    chart.append(hover, ...dots);
    // One shared tooltip node: every render used to append a fresh one to <body> and never removed it.
    const tooltip = document.getElementById("chartTooltip") || h("div", { class: "tooltip", id: "chartTooltip", hidden: true });
    if (!tooltip.isConnected) document.body.append(tooltip);
    tooltip.hidden = true;
    chart.addEventListener("mousemove", (event) => {
      const rect = chart.getBoundingClientRect();
      const px = ((event.clientX - rect.left) / rect.width) * width;
      const index = Math.max(0, Math.min(points.length - 1, Math.round(((px - padL) / (width - padL - padR)) * (points.length - 1))));
      const p = points[index];
      hover.setAttribute("x1", x(index)); hover.setAttribute("x2", x(index)); hover.style.display = "";
      series.forEach(([key], i) => { dots[i].setAttribute("cx", x(index)); dots[i].setAttribute("cy", y(p[key] || 0)); dots[i].style.display = ""; });
      tooltip.hidden = false;
      tooltip.replaceChildren(h("strong", { text: p.day }), h("div", { text: series.map(([key, label]) => `${label}: ${p[key] || 0}`).join(" · ") }));
      tooltip.style.left = `${Math.min(window.innerWidth - 220, event.clientX + 12)}px`;
      tooltip.style.top = `${event.clientY + 12}px`;
    });
    chart.addEventListener("mouseleave", () => { hover.style.display = "none"; dots.forEach((d) => { d.style.display = "none"; }); tooltip.hidden = true; });
    const legend = h("div", { class: "legend" }, ...series.map(([, label, color]) => h("span", {}, h("i", { style: { background: color } }), label)));
    const table = h("details", { style: { marginTop: "8px" } }, h("summary", { class: "muted", text: "Table view" }), h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Day" }), ...series.map(([, label]) => h("th", { class: "num", text: label })))), h("tbody", {}, ...points.map((p) => h("tr", {}, h("td", { text: p.day }), ...series.map(([key]) => h("td", { class: "num", text: String(p[key] || 0) })))))));
    return h("div", {}, chart, legend, table);
  }
})();

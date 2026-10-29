/* Aira — Sites (aira/suite/sites): describe a website, watch it being built, chat to change it, ship it anywhere.
 *
 * #/sites               Build: the prompt box, model and stack pickers, templates and your recent sites
 * #/sites?tab=projects  All sites: search, a table, CSV
 * #/sites?tab=models    Models: the offline designer, Ollama and LM Studio here, Claude, Gemini, OpenAI, OpenRouter,
 *                       any OpenAI-compatible server (keys stay on the server, encrypted)
 * #/sites?project=<id>  The workspace: the conversation with the builder (its steps live while it works) beside
 *                       Preview (desktop, tablet, phone), Code (files, an editor, images), History (every version)
 *                       and Deploy (a public link, a ZIP for any cloud, GitHub, Netlify, Vercel)
 *
 * A build is one POST that answers when the site is written; meanwhile the page reads the run (GET /runs/<id>?from=N)
 * every 400 ms for the text written so far, so the steps, the file being written and its code show as they happen.
 * The preview is an iframe on the site's preview link: the site runs sandboxed in its own opaque origin. DOM is
 * built with h() and textContent only.
 */
"use strict";
(() => {
  const Aira = window.Aira, h = window.h, S = Aira.suite;
  const api = (path, opts) => S.api("sites", path, opts);
  const TABS = [{ id: "build", label: "Build" }, { id: "projects", label: "All sites" }, { id: "models", label: "Models" }];
  const DEVICES = [{ id: "desktop", label: "Desktop", width: "" }, { id: "tablet", label: "Tablet", width: "820px" }, { id: "phone", label: "Phone", width: "390px" }];
  const PANELS = [{ id: "preview", label: "Preview" }, { id: "code", label: "Code" }, { id: "history", label: "History" }, { id: "deploy", label: "Deploy" }];
  const SOURCE_LABEL = { ai: "AI", edit: "Edit", restore: "Restore", template: "Template", copy: "Copy" };
  const CLOUDS = ["Netlify", "Vercel", "Cloudflare Pages", "GitHub Pages", "Firebase", "Google Cloud Run", "AWS Amplify", "AWS S3", "Azure Static Web Apps", "Render", "Fly.io", "Docker"];
  const TOKEN_HELP = {
    github: { url: "https://github.com/settings/tokens/new?scopes=repo&description=Monosage%20Sites", text: "A classic token with the repo scope (or a fine-grained token with Contents and Administration: read and write)." },
    netlify: { url: "https://app.netlify.com/user/applications#personal-access-tokens", text: "User settings > Applications > Personal access tokens." },
    vercel: { url: "https://vercel.com/account/tokens", text: "Account settings > Tokens." },
  };
  let pendingStart = null; // the first build of a site made on the Build page, started when its workspace opens
  let pendingAssets = [];

  const remember = (key, value) => { try { localStorage.setItem(`aira.sites.${key}`, JSON.stringify(value)); } catch { /* storage unavailable */ } };
  const recall = (key, fallback) => { try { const raw = localStorage.getItem(`aira.sites.${key}`); return raw ? JSON.parse(raw) : fallback; } catch { return fallback; } };
  const rid = () => [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const kb = (chars) => (chars < 1024 ? `${chars} B` : `${(chars / 1024).toFixed(1)} KB`);
  const hue = (text) => [...String(text || "")].reduce((sum, ch) => (sum * 31 + ch.charCodeAt(0)) % 360, 7);
  const extOf = (path) => (String(path).split(".").pop() || "").toLowerCase();
  const FILE_ICON = { html: "◇", htm: "◇", css: "#", js: "ƒ", mjs: "ƒ", jsx: "⚛", json: "{}", svg: "◐", md: "¶", txt: "≡", xml: "‹›" };

  // ---------------------------------------------------------------- the model's streamed answer, read in the browser
  const OPEN_RE = /<file\s+path\s*=\s*["']([^"'>]{1,200})["']\s*>/g;
  function readStream(text) {
    const files = [];
    const opens = [...text.matchAll(OPEN_RE)];
    for (const match of opens) {
      const start = match.index + match[0].length;
      const end = text.indexOf("</file>", start);
      files.push({ path: match[1], content: (end < 0 ? text.slice(start) : text.slice(start, end)).replace(/^\n/, ""), done: end >= 0 });
    }
    // A page's own <summary> (a FAQ's <details>) is inside a file block, not the model's summary.
    const talk = text.replace(/<file\s+path\s*=\s*["'][^"'>]{1,200}["']\s*>[\s\S]*?(?:<\/file>|$)/g, " ");
    const plan = (talk.match(/<plan>([\s\S]*?)(?:<\/plan>|$)/) || [])[1] || "";
    const summary = (talk.match(/<summary>([\s\S]*?)(?:<\/summary>|$)/) || [])[1] || "";
    return { files, plan: plan.trim(), summary: summary.trim() };
  }

  // ---------------------------------------------------------------- models: the choice and the picker
  function chosen(providers) {
    const list = providers.providers || [];
    const saved = recall("choice", null);
    const byId = (id) => list.find((p) => p.id === id);
    let provider = saved && byId(saved.provider) ? saved.provider : providers.default;
    if (!byId(provider)) provider = (list[0] || { id: "builtin" }).id;
    const entry = byId(provider) || { models: [], model: "" };
    const model = saved && saved.provider === provider && saved.model ? saved.model : entry.model || entry.default_model || "";
    return { provider, model, stack: (saved && saved.stack) || "html" };
  }
  const providerOf = (providers, id) => (providers.providers || []).find((p) => p.id === id) || { id, label: id, models: [] };
  const choiceLabel = (providers, choice) => {
    const p = providerOf(providers, choice.provider);
    return p.kind === "builtin" || p.kind === "platform" ? p.label : `${p.label.replace(/ \(.*\)$/, "")} · ${choice.model || "choose a model"}`;
  };

  function modelPicker(providers, choice, onChange) {
    const wrap = h("div", { class: "sb-picker" });
    const chip = h("button", { type: "button", class: "sb-chip", "aria-haspopup": "dialog", "aria-expanded": "false", title: "Model" });
    const draw = () => {
      const p = providerOf(providers, choice.provider);
      chip.replaceChildren(h("span", { class: `sb-dot ${p.ready ? "ok" : "warn"}`, "aria-hidden": "true" }), h("span", { class: "sb-chip-text", text: choiceLabel(providers, choice) }), h("span", { "aria-hidden": "true", text: "▾" }));
    };
    let pop = null;
    const close = () => { if (pop) { pop.remove(); pop = null; chip.setAttribute("aria-expanded", "false"); document.removeEventListener("mousedown", outside, true); } };
    const outside = (event) => { if (pop && !wrap.contains(event.target)) close(); };
    const open = () => {
      if (pop) { close(); return; }
      const local = providers.providers.filter((p) => p.local || p.kind === "builtin");
      const cloud = providers.providers.filter((p) => !p.local && p.kind !== "builtin");
      const models = h("datalist", { id: `sbModels${rid().slice(0, 6)}` });
      const modelInput = h("input", { type: "text", class: "sb-model-input", list: models.id, value: choice.model || "", placeholder: "Model name", "aria-label": "Model" });
      const fillModels = async (p) => {
        models.replaceChildren(...(p.models || []).map((m) => h("option", { value: m })));
        modelInput.disabled = p.kind === "builtin" || p.kind === "platform";
        if (!(p.local || p.configured)) return;
        try {
          const found = await api(`/providers/${p.id}/models`, { silent: true });
          if (pop && choice.provider === p.id) models.replaceChildren(...found.models.map((m) => h("option", { value: m })));
          if (found.reachable === false && pop) status.textContent = found.error || "";
          else status.textContent = "";
        } catch { /* the suggestions stay */ }
      };
      const status = h("p", { class: "sb-pop-status muted small", role: "status" });
      const select = (p) => {
        choice.provider = p.id;
        choice.model = p.model || p.default_model || "";
        remember("choice", choice);
        draw(); onChange(choice);
        if (!pop) return;
        modelInput.value = choice.model;
        pop.querySelectorAll(".sb-pop-row").forEach((node) => node.classList.toggle("on", node.dataset.id === p.id));
        fillModels(p);
      };
      const row = (p) => h("button", { type: "button", class: `sb-pop-row${p.id === choice.provider ? " on" : ""}`, dataset: { id: p.id }, onclick: async () => {
        if (!p.ready && (p.needs_key || (p.kind === "openai" && !p.base_url))) {
          close();
          if (!(await editProvider(p, providers))) return;
          const updated = providerOf(providers, p.id);
          if (updated.ready) select(updated);
          return;
        }
        select(p);
      } },
        h("span", { class: "sb-pop-name", text: p.label }),
        h("span", { class: `sb-pop-state ${p.ready ? "ok" : ""}`, text: p.ready ? (p.kind === "builtin" ? "Offline" : "Ready") : p.needs_key ? "Add a key" : "Set up" }));
      const group = (title, items) => (items.length ? h("div", { class: "sb-pop-group" }, h("p", { class: "sb-pop-title", text: title }), ...items.map(row)) : null);
      modelInput.addEventListener("change", () => { choice.model = modelInput.value.trim(); remember("choice", choice); draw(); onChange(choice); });
      pop = h("div", { class: "sb-pop", role: "dialog", "aria-label": "Choose a model", onkeydown: (event) => { if (event.key === "Escape") { event.stopPropagation(); close(); chip.focus(); } } },
        group("On this computer", local), group("In the cloud", cloud),
        h("label", { class: "sb-pop-model" }, h("span", { class: "small muted", text: "Model" }), modelInput, models), status,
        h("div", { class: "sb-pop-foot" }, h("a", { href: "#/sites?tab=models", text: "Manage models and keys" })));
      wrap.append(pop);
      chip.setAttribute("aria-expanded", "true");
      document.addEventListener("mousedown", outside, true);
      fillModels(providerOf(providers, choice.provider));
      const current = pop.querySelector(".sb-pop-row.on") || pop.querySelector(".sb-pop-row");
      if (current) current.focus();
    };
    chip.addEventListener("click", open);
    draw();
    wrap.append(chip);
    wrap.redraw = draw;
    return wrap;
  }

  async function editProvider(p, providers, after) {
    const fields = [];
    if (p.kind !== "builtin" && p.kind !== "platform") {
      fields.push({ name: "base_url", label: "Address", placeholder: p.base_url || "https://…/v1", hint: p.local ? "Where the server runs on this computer." : p.id === "custom" ? "The server's OpenAI-compatible address, ending in /v1." : "Leave as it is unless you use a proxy." });
      fields.push({ name: "model", label: "Model", placeholder: p.default_model || "model name", hint: (p.models || []).length ? `For example ${p.models.slice(0, 3).join(", ")}.` : "The model's name on that server." });
    }
    if (p.needs_key || p.key_optional) fields.push({ name: "api_key", label: p.configured ? `API key (saved, ends in ${p.last4})` : "API key", type: "password", placeholder: p.configured ? "Leave empty to keep the saved key" : "Paste the key", hint: p.key_url ? `Get one at ${p.key_url.replace(/^https:\/\//, "")}` : "" });
    fields.push({ name: "default", label: "Use this model for new builds", type: "checkbox" });
    const saved = await S.edit({
      title: p.label,
      fields: [{ type: "heading", label: p.help || "" }, ...fields],
      values: { base_url: p.base_url, model: p.model, default: providers.default === p.id },
      submitLabel: "Save and test",
      onSave: async (values) => {
        const body = { ...values };
        if (!body.api_key) delete body.api_key;
        const status = await api(`/providers/${p.id}`, { method: "POST", body });
        Object.assign(providers, status);
        try {
          const test = await api(`/providers/${p.id}/test`, { method: "POST", body: {} });
          Aira.toast(`${p.label} answered: “${test.reply}”. Ready to build.`, "success");
        } catch (error) { Aira.toast(`Saved, but the test failed: ${error.message}`, "error", { timeout: 9000 }); }
        return status;
      },
    });
    if (saved && after) after(saved);
    return saved;
  }

  // ---------------------------------------------------------------- the prompt box
  function composer({ placeholder, big = false, providers, choice, stacks, onSubmit, onStop, onAttach, onModel }) {
    const input = h("textarea", { class: "sb-input", rows: big ? 4 : 2, placeholder, "aria-label": placeholder });
    const file = h("input", { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp,image/svg+xml", multiple: true, hidden: true });
    const attached = h("div", { class: "sb-attached" });
    const send = h("button", { type: "submit", class: "sb-send", "aria-label": big ? "Build" : "Send", title: big ? "Build (Ctrl+Enter)" : "Send (Enter)" }, h("span", { "aria-hidden": "true", text: "↑" }), big ? h("span", { text: "Build" }) : null);
    const stop = h("button", { type: "button", class: "sb-stop", hidden: true, onclick: () => onStop && onStop() }, h("span", { class: "sb-stop-square", "aria-hidden": "true" }), h("span", { text: "Stop" }));
    const picker = modelPicker(providers, choice, (next) => onModel && onModel(next));
    const stackSelect = stacks ? Aira.ui.select(stacks.map((s) => [s.id, s.label]), choice.stack || "html", { class: "sb-chip sb-select", "aria-label": "Stack", onchange: (event) => { choice.stack = event.target.value; remember("choice", choice); } }) : null;
    const grow = () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, big ? 320 : 220)}px`; };
    const form = h("form", { class: `sb-composer${big ? " big" : ""}`, onsubmit: (event) => { event.preventDefault(); submit(); } },
      attached, input,
      h("div", { class: "sb-composer-bar" },
        onAttach ? h("button", { type: "button", class: "sb-icon-btn", title: "Add images", "aria-label": "Add images", onclick: () => file.click() }, h("span", { "aria-hidden": "true", text: "＋" })) : null,
        picker, stackSelect, h("span", { class: "sb-grow" }), h("span", { class: "sb-hint small muted", text: big ? "Ctrl+Enter" : "Enter to send · Shift+Enter for a new line" }), stop, send),
      file);
    const submit = () => {
      const text = input.value.trim();
      if (!text || form.classList.contains("busy")) { input.focus(); return; }
      onSubmit(text);
    };
    input.addEventListener("input", grow);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey || (!big && !event.shiftKey))) { event.preventDefault(); submit(); }
    });
    file.addEventListener("change", async () => {
      for (const picked of file.files) {
        try {
          const name = await onAttach(picked);
          if (name) attached.append(h("span", { class: "sb-attach-chip", text: `🖼 ${name}` }));
        } catch (error) { Aira.reportError(error); }
      }
      file.value = "";
    });
    return {
      node: form,
      input,
      picker,
      focus: () => input.focus(),
      clear: () => { input.value = ""; attached.replaceChildren(); grow(); },
      set: (text) => { input.value = text; grow(); input.focus(); },
      busy: (on) => { form.classList.toggle("busy", on); stop.hidden = !on; send.hidden = on; input.disabled = false; },
    };
  }

  const readFile = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.readAsDataURL(file);
  });
  async function upload(projectId, file) {
    if (file.size > 3 * 1024 * 1024) throw new Error(`${file.name} is over 3 MB. Make it smaller first.`);
    const data = await readFile(file);
    const result = await api(`/projects/${projectId}/assets`, { method: "POST", body: { name: file.name, data } });
    return result.asset.name;
  }

  // ---------------------------------------------------------------- pages
  Aira.views.sites = async (main, route) => {
    const query = route.query || {};
    if (query.project) { await workspace(main, query.project); return; }
    const body = S.page(main, { title: "Sites", subtitle: "Describe a website. Watch it being built. Ship it anywhere." });
    const tab = S.tabs(body, route, "sites", TABS);
    if (tab === "projects") await projectsTab(body, query);
    else if (tab === "models") await modelsTab(body);
    else await buildTab(body, query);
  };

  function thumb(project) {
    const box = h("div", { class: "sb-thumb", style: `--hue: ${hue(project.name)}` }); // a custom property needs the style text
    const preview = project.links && project.links.preview;
    if (preview && project.file_count) {
      box.append(h("iframe", { src: preview.url, title: `${project.name} preview`, loading: "lazy", tabindex: "-1", "aria-hidden": "true", sandbox: "allow-scripts", referrerpolicy: "no-referrer" }));
    } else {
      box.append(h("span", { class: "sb-thumb-letter", text: (project.name || "?").slice(0, 1).toUpperCase() }));
    }
    return box;
  }

  function siteCard(project, rerender) {
    const open = () => Aira.router.go(["sites"], { project: project.id });
    const more = h("button", { type: "button", class: "sb-card-more", "aria-label": `More for ${project.name}`, text: "⋯", onclick: async (event) => {
      event.stopPropagation();
      const choice = await menu(event.currentTarget, [["open", "Open"], ["duplicate", "Duplicate"], ["delete", "Delete"]]);
      if (choice === "open") open();
      if (choice === "duplicate") { const copy = await api(`/projects/${project.id}/duplicate`, { method: "POST", body: {} }); Aira.toast(`Made ${copy.project.name}.`, "success"); rerender(); }
      if (choice === "delete" && (await S.confirmDelete(project.name))) { await api(`/projects/${project.id}`, { method: "DELETE" }); Aira.toast("Deleted.", "success"); rerender(); }
    } });
    return h("article", { class: "sb-card", tabindex: "0", onclick: open, onkeydown: (event) => { if (event.key === "Enter" && event.target === event.currentTarget) open(); } },
      thumb(project),
      h("div", { class: "sb-card-body" },
        h("div", { class: "sb-card-title" }, h("strong", { text: project.name }), more),
        h("div", { class: "sb-card-meta muted small" },
          project.published_version ? h("span", { class: "sb-live", text: "● Live" }) : null,
          h("span", { text: project.version ? `Version ${project.version}` : "Not built yet" }),
          h("span", { text: `· ${Aira.fmt.ago(project.updated_at)}` }))));
  }

  function menu(anchor, items) {
    return new Promise((resolve) => {
      const list = h("div", { class: "sb-menu", role: "menu" });
      const done = (value) => { list.remove(); document.removeEventListener("mousedown", outside, true); resolve(value); };
      const outside = (event) => { if (!list.contains(event.target)) done(null); };
      for (const [id, label] of items) list.append(h("button", { type: "button", role: "menuitem", class: id === "delete" ? "danger" : "", text: label, onclick: (event) => { event.stopPropagation(); done(id); } }));
      list.addEventListener("keydown", (event) => { if (event.key === "Escape") done(null); });
      anchor.parentNode.append(list);
      document.addEventListener("mousedown", outside, true);
      list.querySelector("button").focus();
    });
  }

  async function buildTab(body, query) {
    const data = await api("/overview");
    const providers = data.providers;
    const choice = chosen(providers);
    const comp = composer({
      big: true,
      placeholder: "Describe the website you want: who it is for, the pages and sections, the style…",
      providers,
      choice,
      stacks: data.stacks,
      onAttach: async (file) => { pendingAssets.push(file); return file.name; },
      onSubmit: async (prompt) => {
        comp.busy(true);
        try {
          const made = await api("/projects", { method: "POST", body: { prompt, stack: choice.stack } });
          for (const file of pendingAssets) { try { await upload(made.project.id, file); } catch (error) { Aira.reportError(error); } }
          pendingAssets = [];
          pendingStart = { id: made.project.id, prompt, provider: choice.provider, model: choice.model };
          Aira.router.go(["sites"], { project: made.project.id });
        } catch (error) { comp.busy(false); Aira.reportError(error); }
      },
    });
    const ideas = data.templates.slice(0, 6).map((t) => h("button", { type: "button", class: "sb-idea", text: t.name, title: t.prompt, onclick: () => comp.set(t.prompt) }));
    body.append(
      h("section", { class: "sb-hero" },
        h("div", { class: "sb-hero-glow", "aria-hidden": "true" }),
        h("h2", { class: "sb-hero-title", text: "What do you want to build?" }),
        h("p", { class: "sb-hero-sub", text: "Describe a website in your own words. Monosage designs and codes it while you watch, then you chat to change anything." }),
        comp.node,
        h("div", { class: "sb-ideas" }, h("span", { class: "muted small", text: "Try:" }), ...ideas)),
    );
    if (data.recent.length) {
      const rerender = () => Aira.router.render();
      body.append(S.section("Your sites", h("div", { class: "sb-cards" }, ...data.recent.map((p) => siteCard(p, rerender))), data.sites > data.recent.length ? h("p", {}, h("a", { href: "#/sites?tab=projects", text: `See all ${data.sites} sites` })) : null));
    }
    const templates = h("div", { class: "sb-templates", id: "sbTemplates" }, ...data.templates.map((t) => h("button", { type: "button", class: "sb-template", style: `--hue: ${hue(t.id)}`, onclick: async (event) => {
      await Aira.withBusy(event.currentTarget, async () => {
        const made = await api("/projects", { method: "POST", body: { template: t.id } });
        Aira.router.go(["sites"], { project: made.project.id });
      });
    } }, h("span", { class: "sb-template-art", "aria-hidden": "true" }, h("span"), h("span"), h("span")), h("strong", { text: t.name }), h("span", { class: "muted small", text: t.prompt }))));
    body.append(S.section("Start from a template", h("p", { class: "muted", text: "A finished site in one click, built offline. Change it by chatting, with or without a model." }), templates));
    body.append(S.section("Your numbers", S.kpis([
      { label: "Sites", value: data.sites },
      { label: "Published", value: data.published },
      { label: "Builds this month", value: data.builds_this_month },
      { label: "Client sites paid", value: S.money(data.paid_cents), tone: data.paid_cents ? "good" : "" },
    ]), h("p", { class: "muted small", text: "Build sites for clients: set the client and price in a site's Details and mark it paid. It joins the Business hub's revenue." })));
    if (query.new || query.template) requestAnimationFrame(() => (query.template ? templates.scrollIntoView({ block: "start" }) : comp.focus()));
    else comp.focus();
  }

  async function projectsTab(body, query) {
    const search = h("input", { type: "search", class: "sb-search", placeholder: "Search sites, clients…", value: query.q || "", "aria-label": "Search sites" });
    const list = h("div");
    const draw = async () => {
      const data = await api(`/projects?${new URLSearchParams({ q: search.value.trim() })}`);
      list.replaceChildren(S.table({
        caption: "Sites",
        columns: [
          { key: "name", label: "Site", render: (p) => h("span", { class: "sb-row-name" }, h("strong", { text: p.name }), p.description ? h("span", { class: "muted small", text: p.description }) : null) },
          { key: "client", label: "Client" },
          { key: "price_cents", label: "Price", num: true, render: (p) => (p.price_cents ? h("span", {}, S.money(p.price_cents), " ", S.pill(p.paid ? "paid" : "pending")) : "") },
          { key: "published_version", label: "Live", render: (p) => (p.published_version ? S.pill("live") : S.pill("draft")) },
          { key: "version", label: "Version", num: true },
          { key: "updated_at", label: "Updated", render: (p) => Aira.fmt.ago(p.updated_at) },
        ],
        rows: data.projects,
        onRow: (p) => Aira.router.go(["sites"], { project: p.id }),
        empty: h("div", { class: "empty" }, h("strong", { text: search.value ? "No site matches" : "No sites yet" }), h("div", { text: "Describe one on the Build page and it is ready in a minute." }), h("a", { class: "btn primary", href: "#/sites", text: "Build a site" })),
      }));
    };
    let timer = 0;
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => draw().catch(Aira.reportError), 250); });
    body.append(h("div", { class: "sb-toolbar" }, search, h("a", { class: "btn secondary", href: "/api/sites/projects.csv", text: "Download CSV" }), h("a", { class: "btn primary", href: "#/sites?new=1", text: "New site" })), list);
    await draw();
  }

  async function modelsTab(body) {
    const providers = await api("/providers");
    const grid = h("div", { class: "sb-models" });
    const draw = () => {
      grid.replaceChildren(...providers.providers.map((p) => h("article", { class: `card sb-model${providers.default === p.id ? " default" : ""}` },
        h("div", { class: "sb-model-head" },
          h("strong", { text: p.label }),
          h("span", { class: `suite-pill ${p.ready ? "good" : "warn"}`, text: p.ready ? (providers.default === p.id ? "Default" : "Ready") : p.needs_key ? "Needs a key" : "Not set up" })),
        h("p", { class: "muted small", text: p.help }),
        p.kind !== "builtin" && p.kind !== "platform" ? Aira.ui.kv([["Address", p.base_url || "—"], ["Model", p.model || "—"], ...(p.needs_key || p.key_optional ? [["Key", p.configured ? `saved, ends in ${p.last4}` : "none"]] : [])]) : null,
        h("div", { class: "btn-row" },
          p.kind !== "builtin" && p.kind !== "platform" ? S.button(p.configured || p.saved ? "Edit" : "Set up", () => editProvider(p, providers, draw), { kind: "secondary sm" }) : null,
          S.button("Test", async () => { const test = await api(`/providers/${p.id}/test`, { method: "POST", body: {} }); Aira.toast(`${p.label}: “${test.reply}”`, "success"); }, { kind: "ghost sm" }),
          providers.default !== p.id ? S.button("Make default", async () => { Object.assign(providers, await api("/default-provider", { method: "POST", body: { provider: p.id } })); remember("choice", { ...recall("choice", {}), provider: p.id, model: p.model || p.default_model }); draw(); }, { kind: "ghost sm" }) : null,
          p.configured ? S.button("Remove key", async () => { if (await Aira.confirm({ title: "Remove the key", message: `Forget the ${p.label} key? Builds with it stop until you add one again.`, confirmLabel: "Remove", danger: true })) { Object.assign(providers, await api(`/providers/${p.id}/key`, { method: "DELETE" })); draw(); } }, { kind: "ghost sm" }) : null,
          p.key_url ? h("a", { class: "btn ghost sm", href: p.key_url, target: "_blank", rel: "noopener noreferrer", text: p.local ? "Download" : "Get a key" }) : null))));
    };
    draw();
    body.append(
      h("p", { class: "muted", text: providers.hosted ? "Pick the model that writes your sites. Keys are stored encrypted for your organization and never shown again." : "Pick the model that writes your sites. Local models are free and private; cloud models are the most capable. Keys are stored encrypted on this computer and never shown again." }),
      grid);
  }

  // ---------------------------------------------------------------- the workspace
  async function workspace(main, projectId) {
    let project = (await api(`/projects/${projectId}`)).project;
    const providers = await api("/providers");
    const choice = chosen(providers);
    if (project.provider && providers.providers.some((p) => p.id === project.provider) && !pendingStart) {
      choice.provider = project.provider;
      choice.model = project.model || providerOf(providers, project.provider).model;
    }
    if (Aira.shell) Aira.shell.tabs("sites", TABS, "projects");
    document.title = `${project.name} · Sites · Monosage`;
    const state = { panel: recall("panel", "preview"), device: recall("device", "desktop"), page: "index.html", file: "index.html", version: null, run: null, edits: {}, autoCode: false, reload: 0, view: "chat" };

    const wrap = h("div", { class: "sb-ws" });
    const status = h("span", { class: "sb-status", role: "status" });
    const nameButton = h("button", { type: "button", class: "sb-ws-name", title: "Rename", onclick: () => details() });
    const liveLink = h("a", { class: "sb-live-link", target: "_blank", rel: "noopener noreferrer", hidden: true });
    const head = h("header", { class: "sb-ws-head" },
      h("a", { class: "sb-back", href: "#/sites", "aria-label": "All sites", title: "All sites", text: "←" }),
      nameButton, status, h("span", { class: "sb-grow" }), liveLink,
      S.button("Details", () => details(), { kind: "ghost sm" }),
      S.button("Publish", () => { state.panel = "deploy"; drawPanel(); }, { kind: "primary sm" }));
    const switcher = h("div", { class: "sb-switch", role: "tablist", "aria-label": "Show" },
      ...["chat", "site"].map((id) => h("button", { type: "button", role: "tab", "data-view": id, "aria-selected": String(state.view === id), text: id === "chat" ? "Chat" : "Site", onclick: () => { state.view = id; wrap.dataset.view = id; switcher.querySelectorAll("button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === id))); } })));
    const messages = h("div", { class: "sb-messages", "aria-live": "polite" });
    const comp = composer({
      placeholder: "Ask for a change: “add a pricing page”, “make the hero bolder”, “use our logo”…",
      providers,
      choice,
      onSubmit: (text) => build(text),
      onStop: () => stopRun(),
      onAttach: async (file) => { const name = await upload(project.id, file); await reload(); Aira.toast(`Added assets/${name}. Mention it in your request.`, "success"); return name; },
    });
    const chat = h("section", { class: "sb-chat", "aria-label": "Conversation" }, messages, comp.node);
    const panelTabs = h("div", { class: "sb-panel-tabs", role: "tablist" });
    const tools = h("div", { class: "sb-tools" });
    const panelBody = h("div", { class: "sb-panel-body" });
    const view = h("section", { class: "sb-view", "aria-label": "Site" }, h("div", { class: "sb-view-bar" }, panelTabs, h("span", { class: "sb-grow" }), tools), panelBody);
    wrap.dataset.view = state.view;
    wrap.append(head, switcher, h("div", { class: "sb-split" }, chat, view));
    main.replaceChildren(wrap);

    const drawHead = () => {
      nameButton.textContent = project.name;
      const site = project.links.site;
      liveLink.hidden = !site;
      if (site) { liveLink.href = site.url; liveLink.textContent = `● Live · v${project.published_version}`; }
      if (state.run) status.replaceChildren(h("span", { class: "sb-spin", "aria-hidden": "true" }), h("span", { text: "Building…" }));
      else status.textContent = project.version ? `Version ${project.version} · saved` : "Not built yet";
    };

    // -------- conversation
    const avatar = () => h("span", { class: "sb-avatar", "aria-hidden": "true", text: "M" });
    function messageNode(message) {
      if (message.role === "user") return h("div", { class: "sb-msg user" }, h("div", { class: "sb-bubble", translate: "no", text: message.content }));
      const meta = message.meta || {};
      const failed = meta.status === "failed" || meta.status === "cancelled";
      const changed = meta.changed || [];
      return h("div", { class: `sb-msg bot${failed ? " failed" : ""}` }, avatar(),
        h("div", { class: "sb-bot" },
          meta.plan && meta.plan !== message.content ? h("p", { class: "sb-plan muted small", text: meta.plan }) : null,
          h("p", { class: "sb-reply", text: message.content }),
          changed.length ? h("div", { class: "sb-files" }, ...changed.map((path) => h("button", { type: "button", class: "sb-file-chip", translate: "no", text: `${FILE_ICON[extOf(path)] || "·"} ${path}`, onclick: () => { state.panel = "code"; state.file = path; drawPanel(); if (state.view === "chat") switcher.querySelector('[data-view="site"]').click(); } }))) : null,
          h("div", { class: "sb-msg-meta small muted" },
            meta.provider_label ? h("span", { text: `${meta.provider_label.replace(/ \(.*\)$/, "")}${meta.model && meta.provider !== "builtin" ? ` · ${meta.model}` : ""}${meta.seconds ? ` · ${meta.seconds}s` : ""}` }) : null,
            message.version ? h("span", { class: "sb-ver", text: `v${message.version}` }) : null,
            message.version && message.version !== project.version ? h("button", { type: "button", class: "sb-link", text: "Preview", onclick: () => { state.version = message.version; state.panel = "preview"; drawPanel(); } }) : null,
            message.version && message.version !== project.version ? h("button", { type: "button", class: "sb-link", text: "Restore", onclick: () => restore(message.version) }) : null,
            failed && !state.run ? h("button", { type: "button", class: "sb-link", text: "Try again", onclick: () => { const last = [...project.messages].reverse().find((m) => m.role === "user"); if (last) build(last.content); } }) : null)));
    }

    const agent = h("div", { class: "sb-msg bot sb-agent" });
    function drawAgent() {
      if (!state.run) { agent.remove(); return; }
      const run = state.run;
      const read = readStream(run.output);
      const seconds = Math.round((Date.now() - run.started) / 1000);
      const step = (mark, label, detail) => h("li", { class: `sb-step ${mark}` }, h("span", { class: "sb-step-mark", "aria-hidden": "true" }, mark === "doing" ? h("span", { class: "sb-spin" }) : mark === "done" ? "✓" : ""), h("span", { class: "sb-step-label", text: label }), detail ? h("span", { class: "sb-step-detail muted small", text: detail }) : null);
      const started = run.output.length > 0;
      const steps = [step(started ? "done" : "doing", started ? "Read your request" : "Thinking about your request")];
      if (read.plan || run.phase === "planning") steps.push(step(read.files.length ? "done" : "doing", "Planning", read.plan));
      for (const file of read.files) steps.push(step(file.done ? "done" : "doing", `${file.done ? "Wrote" : "Writing"} ${file.path}`, kb(file.content.length)));
      if (run.phase === "finishing" || read.summary) steps.push(step("doing", "Finishing up"));
      agent.replaceChildren(avatar(), h("div", { class: "sb-bot" },
        h("div", { class: "sb-agent-head" }, h("strong", { text: "Building" }), h("span", { class: "muted small", text: ` · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} · ${choiceLabel(providers, { provider: run.provider, model: run.model })}` })),
        h("ol", { class: "sb-steps" }, ...steps),
        !started && seconds > 20 && providerOf(providers, run.provider).local ? h("p", { class: "muted small", text: "A local model can take a minute to load the first time." }) : null));
      if (!agent.isConnected) messages.append(agent);
      messages.scrollTop = messages.scrollHeight;
    }

    function drawMessages() {
      const list = project.messages || [];
      if (!list.length && !state.run) {
        messages.replaceChildren(h("div", { class: "sb-welcome" }, avatar(), h("div", {},
          h("strong", { text: `Let's build ${project.name}.` }),
          h("p", { class: "muted", text: "Describe the site: who it is for, its pages and sections, the look you like. Add images with ＋." }))));
      } else {
        messages.replaceChildren(...list.map(messageNode));
      }
      drawAgent();
      messages.scrollTop = messages.scrollHeight;
    }

    // -------- building
    async function build(prompt) {
      if (state.run) return;
      if (Object.keys(state.edits).length && !(await Aira.confirm({ title: "Unsaved code", message: "You have code changes that are not saved. Build anyway? They will be lost.", confirmLabel: "Build anyway" }))) return;
      state.edits = {};
      const runId = rid();
      state.run = { id: runId, output: "", phase: "thinking", status: "running", provider: choice.provider, model: choice.model, started: Date.now(), from: 0 };
      state.autoCode = state.panel === "preview";
      project.messages = [...(project.messages || []), { id: `local-${runId}`, role: "user", content: prompt }];
      comp.clear();
      comp.busy(true);
      drawHead(); drawMessages();
      const request = api(`/projects/${project.id}/generate`, { method: "POST", body: { prompt, provider: choice.provider, model: choice.model, run_id: runId }, silent: true });
      const ticker = setInterval(() => { if (state.run && state.run.id === runId) drawAgent(); }, 1000);
      const polling = poll(runId);
      try {
        const result = await request;
        project = result.project;
        const changed = (result.message.meta || {}).changed || [];
        if (result.run.status === "done" && result.version) Aira.toast(`Version ${result.version} is ready.`, "success");
        if (changed.length && !changed.includes(state.file)) state.file = changed.find((p) => p.endsWith(".html")) || changed[0];
        if (state.autoCode && state.panel === "code") state.panel = "preview";
        state.version = null;
      } catch (error) {
        // A proxy may cut a long request (502/504, or no answer at all): the server finishes the build anyway,
        // so keep reading the run until it ends and then show the result.
        if ([0, 502, 503, 504].includes(error.status) && state.run && state.run.id === runId) {
          await polling;
          Aira.toast("The build finished while the connection was interrupted.", "info");
        } else {
          Aira.reportError(error);
        }
        await reload(false);
      } finally {
        clearInterval(ticker);
        state.run = null;
        comp.busy(false);
        state.reload += 1;
        drawHead(); drawMessages(); drawPanel();
        comp.focus();
      }
    }

    async function poll(runId) {
      await sleep(500); // the build request records its run first; reading before that would be a 404
      let misses = 0;
      while (state.run && state.run.id === runId && wrap.isConnected) {
        try {
          const { run } = await api(`/runs/${runId}?from=${state.run.from}`, { silent: true });
          misses = 0;
          if (!state.run || state.run.id !== runId) return;
          state.run.output += run.output;
          state.run.from = run.length;
          Object.assign(state.run, { phase: run.phase, status: run.status, current: run.current_file });
          drawAgent();
          if (state.autoCode && state.panel === "preview" && readStream(state.run.output).files.length) { state.panel = "code"; drawPanel(); }
          if (state.panel === "code") streamCode();
          if (run.status !== "running") return;
        } catch {
          misses += 1; // not recorded yet, or a network blip: read again, but not forever (it never reached the server)
          if (misses > 25) return;
        }
        await sleep(400);
      }
    }

    async function resume(running) {
      state.run = { id: running.id, output: "", phase: running.phase, status: "running", provider: running.provider, model: running.model, started: Date.parse(running.started_at) || Date.now(), from: 0 };
      comp.busy(true); drawHead(); drawMessages();
      await poll(running.id);
      state.run = null;
      comp.busy(false);
      await reload();
    }

    async function stopRun() {
      if (!state.run) return;
      await api(`/runs/${state.run.id}/cancel`, { method: "POST", body: {} });
      Aira.toast("Stopping…", "info");
    }

    async function reload(draw = true) {
      project = (await api(`/projects/${project.id}`)).project;
      if (draw) { state.reload += 1; drawHead(); drawMessages(); drawPanel(); }
    }

    async function restore(number) {
      if (!(await Aira.confirm({ title: `Restore version ${number}`, message: `The site goes back to version ${number}. Nothing is lost: the current files stay in History as version ${project.version}.`, confirmLabel: "Restore" }))) return;
      const result = await api(`/projects/${project.id}/versions/${number}/restore`, { method: "POST", body: {} });
      project = result.project;
      state.version = null; state.edits = {}; state.reload += 1;
      Aira.toast(`Restored. This is version ${result.version}.`, "success");
      drawHead(); drawMessages(); drawPanel();
    }

    async function details() {
      const saved = await S.edit({
        title: "Site details",
        fields: [
          { name: "name", label: "Name", required: true },
          { name: "description", label: "Description", type: "textarea", rows: 2 },
          { name: "stack", label: "Stack for new changes", type: "select", options: [["html", "HTML, CSS and JavaScript"], ["tailwind", "Tailwind CSS"], ["react", "React (no build step)"]] },
          { type: "heading", label: "Client work" },
          { name: "client", label: "Client", placeholder: "Who the site is for" },
          { name: "price_cents", label: "Price", type: "money", hint: "What the client pays. Mark it paid in Deploy > Client payment." },
        ],
        values: project,
        onSave: async (values) => (await api(`/projects/${project.id}`, { method: "PATCH", body: values })).project,
      });
      if (saved) { project = { ...project, ...saved }; drawHead(); drawPanel(); }
    }

    // -------- the right panel
    function drawPanelTabs() {
      panelTabs.replaceChildren(...PANELS.map((p) => h("button", { type: "button", role: "tab", class: "sb-ptab", "aria-selected": String(state.panel === p.id), text: p.label, onclick: () => { state.panel = p.id; state.autoCode = false; remember("panel", p.id === "deploy" ? "preview" : p.id); drawPanel(); } })));
    }

    function previewUrl() {
      const link = project.links.preview;
      if (!link) return "";
      const base = state.version ? link.url.replace(/\/w\/$/, `/v/${state.version}/`) : link.url;
      return `${base}${state.page === "index.html" ? "" : state.page}`;
    }

    function drawPanel() {
      drawPanelTabs();
      tools.replaceChildren();
      if (state.panel === "code") return drawCode();
      if (state.panel === "history") return drawHistory();
      if (state.panel === "deploy") return drawDeploy();
      return drawPreview();
    }

    function drawPreview() {
      tools.replaceChildren();
      const pages = Object.keys(project.files || {}).filter((p) => /\.html?$/.test(p));
      if (!state.version && !pages.length) {
        panelBody.replaceChildren(h("div", { class: "sb-empty-stage" }, h("div", { class: "sb-empty-art", "aria-hidden": "true" }, h("span"), h("span"), h("span")), h("strong", { text: state.run ? "Your site is being written…" : "Your site appears here" }), h("p", { class: "muted", text: state.run ? "Watch the steps on the left, or open Code to see it being written." : "Describe it on the left and press Enter." })));
        return;
      }
      if (pages.length > 1) tools.append(Aira.ui.select(pages.map((p) => [p, p]), state.page, { class: "sb-chip sb-select", "aria-label": "Page", onchange: (event) => { state.page = event.target.value; drawPreview(); } }));
      tools.append(h("div", { class: "sb-devices", role: "group", "aria-label": "Screen size" }, ...DEVICES.map((d) => h("button", { type: "button", class: "sb-icon-btn", "aria-pressed": String(state.device === d.id), title: d.label, "aria-label": d.label, text: d.id === "desktop" ? "🖥" : d.id === "tablet" ? "▭" : "▯", onclick: () => { state.device = d.id; remember("device", d.id); drawPreview(); } }))),
        h("button", { type: "button", class: "sb-icon-btn", title: "Reload", "aria-label": "Reload", text: "⟳", onclick: () => { state.reload += 1; drawPreview(); } }),
        h("a", { class: "sb-icon-btn", href: previewUrl(), target: "_blank", rel: "noopener noreferrer", title: "Open in a new tab", "aria-label": "Open in a new tab", text: "↗" }));
      const device = DEVICES.find((d) => d.id === state.device) || DEVICES[0];
      const src = `${previewUrl()}${previewUrl().includes("?") ? "&" : "?"}r=${state.reload}`;
      const frame = h("iframe", { class: "sb-frame", src, title: `Preview of ${project.name}`, sandbox: "allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals", referrerpolicy: "no-referrer" });
      panelBody.replaceChildren(...[
        state.version ? h("div", { class: "sb-banner" }, h("span", { text: `Previewing version ${state.version}.` }), h("button", { type: "button", class: "sb-link", text: "Back to the latest", onclick: () => { state.version = null; drawPreview(); } }), h("button", { type: "button", class: "sb-link", text: "Restore this version", onclick: () => restore(state.version) })) : null,
        h("div", { class: `sb-stage ${device.id}` }, h("div", { class: "sb-device", style: { maxWidth: device.width || "none" } }, frame))].filter(Boolean));
    }

    // Code: the file list, an editor with line numbers, images; while a build runs, the file being written.
    let editor = null;
    function streamCode() {
      if (!state.run || state.panel !== "code") return;
      const read = readStream(state.run.output);
      const writing = read.files[read.files.length - 1];
      if (!writing) return;
      if (!editor || editor.dataset.stream !== writing.path) { drawCode(); return; }
      editor.value = writing.content;
      editor.scrollTop = editor.scrollHeight;
      editor.dispatchEvent(new Event("sb:lines"));
    }

    function drawCode() {
      const live = state.run ? readStream(state.run.output).files : [];
      const writing = live.length && !live[live.length - 1].done ? live[live.length - 1] : null;
      const paths = [...new Set([...Object.keys(project.files || {}), ...live.map((f) => f.path)])].sort((a, b) => (a === "index.html" ? -1 : b === "index.html" ? 1 : a.localeCompare(b)));
      if (writing) state.file = writing.path;
      if (!paths.includes(state.file)) state.file = paths[0] || "";
      const tree = h("nav", { class: "sb-tree", "aria-label": "Files" },
        h("div", { class: "sb-tree-head" }, h("strong", { text: "Files" }), h("span", { class: "sb-grow" }),
          h("button", { type: "button", class: "sb-icon-btn", title: "New file", "aria-label": "New file", text: "＋", disabled: !!state.run, onclick: () => newFile() })),
        ...paths.map((path) => h("button", { type: "button", class: `sb-tree-item${path === state.file ? " on" : ""}`, "aria-current": path === state.file ? "true" : "false", onclick: () => { state.file = path; drawCode(); } },
          h("span", { class: `sb-ficon ${extOf(path)}`, "aria-hidden": "true", text: FILE_ICON[extOf(path)] || "·" }),
          h("span", { class: "sb-tree-name", translate: "no", text: path }),
          state.edits[path] !== undefined ? h("span", { class: "sb-unsaved", title: "Not saved", text: "●" }) : null,
          writing && writing.path === path ? h("span", { class: "sb-spin", "aria-label": "Being written" }) : null)),
        (project.assets || []).length ? h("div", { class: "sb-tree-group muted small", text: "Images" }) : null,
        ...(project.assets || []).map((asset) => h("button", { type: "button", class: `sb-tree-item${state.file === `assets/${asset.name}` ? " on" : ""}`, onclick: () => { state.file = `assets/${asset.name}`; drawCode(); } }, h("span", { class: "sb-ficon", "aria-hidden": "true", text: "🖼" }), h("span", { class: "sb-tree-name", text: asset.name }))));
      let pane;
      if (state.file.startsWith("assets/") && !paths.includes(state.file)) {
        const asset = (project.assets || []).find((a) => `assets/${a.name}` === state.file);
        pane = h("div", { class: "sb-editor" }, h("div", { class: "sb-editor-head" }, h("strong", { text: state.file }), h("span", { class: "sb-grow" }),
          h("button", { type: "button", class: "btn ghost sm", text: "Copy path", onclick: () => S.copy(state.file) }),
          S.button("Delete", async () => { if (await S.confirmDelete(state.file)) { await api(`/projects/${project.id}/assets/${encodeURIComponent(asset.name)}`, { method: "DELETE" }); state.file = "index.html"; await reload(); } }, { kind: "ghost sm" })),
          h("div", { class: "sb-asset" }, h("img", { src: `${project.links.preview.url}${state.file}`, alt: asset ? asset.name : "" }), h("p", { class: "muted small", text: asset ? `${asset.mime} · ${kb(asset.size)} · use it as ${state.file}` : "" })));
      } else if (state.file) {
        const streaming = !!(writing && writing.path === state.file);
        const liveFile = live.find((f) => f.path === state.file);
        const text = state.edits[state.file] !== undefined ? state.edits[state.file] : streaming ? writing.content : (project.files || {})[state.file] !== undefined ? project.files[state.file] : liveFile ? liveFile.content : "";
        const gutter = h("pre", { class: "sb-gutter", "aria-hidden": "true", translate: "no" });
        editor = h("textarea", { class: "sb-code-area", translate: "no", spellcheck: "false", autocapitalize: "off", autocomplete: "off", wrap: "off", "aria-label": `Code of ${state.file}`, readonly: state.run ? true : null });
        editor.value = text;
        editor.dataset.stream = streaming ? state.file : "";
        const lines = () => { const count = editor.value.split("\n").length; gutter.textContent = Array.from({ length: count }, (_, i) => i + 1).join("\n"); gutter.scrollTop = editor.scrollTop; };
        editor.addEventListener("sb:lines", lines);
        editor.addEventListener("scroll", () => { gutter.scrollTop = editor.scrollTop; });
        editor.addEventListener("input", () => { state.edits[state.file] = editor.value; lines(); unsaved.hidden = false; });
        editor.addEventListener("keydown", (event) => {
          if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); save(); }
          if (event.key === "Tab" && !event.shiftKey && !event.ctrlKey) { event.preventDefault(); const { selectionStart: a, selectionEnd: b, value } = editor; editor.value = `${value.slice(0, a)}  ${value.slice(b)}`; editor.selectionStart = editor.selectionEnd = a + 2; editor.dispatchEvent(new Event("input")); }
        });
        const unsaved = h("span", { class: "sb-unsaved small", hidden: state.edits[state.file] === undefined, text: "● Not saved" });
        const path = state.file;
        const save = async () => {
          if (state.edits[path] === undefined) return;
          const result = await api(`/projects/${project.id}/files`, { method: "PUT", body: { path, content: state.edits[path] } });
          project.files[path] = state.edits[path];
          delete state.edits[path];
          project.version = result.version;
          unsaved.hidden = true;
          state.reload += 1;
          drawHead();
          Aira.toast(`Saved ${path} · version ${result.version}`, "success");
        };
        pane = h("div", { class: "sb-editor" },
          h("div", { class: "sb-editor-head" },
            h("strong", { text: state.file }), streaming ? h("span", { class: "sb-writing small", text: "Writing…" }) : unsaved, h("span", { class: "sb-grow" }),
            !state.run ? S.button("Save", save, { kind: "primary sm", title: "Save (Ctrl+S)" }) : null,
            !state.run && state.file !== "index.html" && project.files[state.file] !== undefined ? h("button", { type: "button", class: "btn ghost sm", text: "Rename", onclick: () => renameFile(state.file) }) : null,
            !state.run && state.file !== "index.html" && project.files[state.file] !== undefined ? S.button("Delete", async () => { if (await S.confirmDelete(state.file)) { await api(`/projects/${project.id}/files/delete`, { method: "POST", body: { path: state.file } }); delete state.edits[state.file]; state.file = "index.html"; await reload(); } }, { kind: "ghost sm" }) : null),
          h("div", { class: "sb-code" }, gutter, editor));
        requestAnimationFrame(() => { lines(); if (streaming) { editor.scrollTop = editor.scrollHeight; gutter.scrollTop = editor.scrollTop; } });
      } else {
        pane = h("div", { class: "sb-empty-stage" }, h("strong", { text: "No files yet" }), h("p", { class: "muted", text: "Build the site, or add a file with ＋." }));
      }
      panelBody.replaceChildren(h("div", { class: "sb-codebox" }, tree, pane));
    }

    async function newFile() {
      const saved = await S.edit({ title: "New file", fields: [{ name: "path", label: "File name", required: true, placeholder: "about.html, css/extra.css, data.json" }], onSave: async (values) => {
        const result = await api(`/projects/${project.id}/files`, { method: "PUT", body: { path: values.path, content: values.path.endsWith(".html") ? `<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>New page</title>\n  <link rel="stylesheet" href="styles.css">\n</head>\n<body>\n\n</body>\n</html>\n` : "\n" } });
        return result;
      } });
      if (saved) { state.file = saved.path; await reload(); state.panel = "code"; drawPanel(); }
    }

    async function renameFile(path) {
      const saved = await S.edit({ title: `Rename ${path}`, fields: [{ name: "to", label: "New name", required: true }], values: { to: path }, onSave: async (values) => api(`/projects/${project.id}/files/rename`, { method: "POST", body: { from: path, to: values.to } }) });
      if (saved) { delete state.edits[path]; state.file = saved.path; await reload(); }
    }

    function drawHistory() {
      const versions = project.versions || [];
      if (!versions.length) { panelBody.replaceChildren(h("div", { class: "sb-empty-stage" }, h("strong", { text: "No versions yet" }), h("p", { class: "muted", text: "Every build and every saved edit becomes a version you can preview, restore or publish." }))); return; }
      panelBody.replaceChildren(h("ol", { class: "sb-history" }, ...versions.map((v) => h("li", { class: `sb-version${v.number === project.version ? " current" : ""}` },
        h("span", { class: "sb-vnum", text: `v${v.number}` }),
        h("div", { class: "sb-vbody" },
          h("div", {}, h("strong", { text: v.label || `Version ${v.number}` }), " ", h("span", { class: `suite-pill ${v.source === "ai" ? "info" : ""}`, text: SOURCE_LABEL[v.source] || v.source }),
            v.number === project.version ? h("span", { class: "suite-pill good", text: "current" }) : null,
            v.number === project.published_version ? h("span", { class: "suite-pill good", text: "live" }) : null),
          h("div", { class: "muted small", text: `${Aira.fmt.ago(v.updated_at || v.created_at)}${(v.changed || []).length ? ` · ${v.changed.slice(0, 6).join(", ")}${v.changed.length > 6 ? "…" : ""}` : ""}` })),
        h("div", { class: "btn-row" },
          h("button", { type: "button", class: "btn ghost sm", text: "Preview", onclick: () => { state.version = v.number === project.version ? null : v.number; state.panel = "preview"; drawPanel(); } }),
          v.number !== project.version ? S.button("Restore", () => restore(v.number), { kind: "ghost sm" }) : null,
          v.number !== project.published_version ? S.button("Publish", async () => { const result = await api(`/projects/${project.id}/publish`, { method: "POST", body: { version: v.number } }); project = result.project; Aira.toast(`Version ${v.number} is live.`, "success"); drawHead(); drawHistory(); }, { kind: "ghost sm" }) : null,
          h("a", { class: "btn ghost sm", href: `/api/sites/projects/${project.id}/export.zip?version=${v.number}`, text: "ZIP" }))))));
    }

    async function drawDeploy() {
      panelBody.replaceChildren(Aira.ui.skeleton(3));
      const targets = (await api("/deploy-targets")).targets;
      const site = project.links.site;
      const saved = project.deploys || {};
      const publishCard = h("section", { class: "card sb-deploy-card" },
        h("div", { class: "sb-deploy-head" }, h("span", { class: "sb-deploy-icon", "aria-hidden": "true", text: "🌐" }), h("div", {}, h("strong", { text: "Publish to a link" }), h("p", { class: "muted small", text: "Anyone with the link sees the site. Update it whenever you like; take it offline any time." }))),
        site ? h("div", { class: "sb-live-box" },
          h("a", { href: site.url, target: "_blank", rel: "noopener noreferrer", class: "sb-url", text: S.absolute(site.url) }),
          h("span", { class: "muted small", text: `Version ${project.published_version} · ${site.views || 0} visits` })) : null,
        h("div", { class: "btn-row" },
          S.button(site ? (project.published_version === project.version ? "Published" : `Update to version ${project.version}`) : "Publish", async () => {
            const result = await api(`/projects/${project.id}/publish`, { method: "POST", body: {} });
            project = result.project;
            Aira.toast(`Live at ${S.absolute(result.link.url)}`, "success");
            drawHead(); drawDeploy();
          }, { kind: "primary" }),
          site ? S.button("Copy link", () => S.copy(S.absolute(site.url)), { kind: "secondary" }) : null,
          site ? S.button("Take offline", async () => { if (await Aira.confirm({ title: "Take the site offline", message: "The link stops working. You can publish again later (with a new link).", confirmLabel: "Take offline", danger: true })) { project = (await api(`/projects/${project.id}/publish`, { method: "DELETE" })).project; drawHead(); drawDeploy(); } }, { kind: "ghost" }) : null));
      const withKit = h("input", { type: "checkbox", checked: true });
      const zipCard = h("section", { class: "card sb-deploy-card" },
        h("div", { class: "sb-deploy-head" }, h("span", { class: "sb-deploy-icon", "aria-hidden": "true", text: "📦" }), h("div", {}, h("strong", { text: "Download for any cloud" }), h("p", { class: "muted small", text: "A ZIP of the site with ready-made settings for every major host and a README with the one command each needs." }))),
        h("div", { class: "sb-clouds" }, ...CLOUDS.map((c) => h("span", { class: "sb-cloud", text: c }))),
        h("label", { class: "suite-check" }, withKit, h("span", { text: "Include the deploy kit (Dockerfile, netlify.toml, vercel.json, wrangler.toml, firebase.json…)" })),
        h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary", text: "Download ZIP", onclick: () => { location.href = `/api/sites/projects/${project.id}/export.zip${withKit.checked ? "" : "?kit=0"}`; } })));
      const targetCard = (target) => {
        const t = targets.find((x) => x.id === target);
        const help = TOKEN_HELP[target];
        const last = saved[target] || {};
        const icon = { github: "🐙", netlify: "◆", vercel: "▲" }[target];
        const intro = { github: "Push the whole site to a repository as one commit, and optionally publish it with GitHub Pages.", netlify: "Deploy straight to Netlify. The next deploy updates the same site.", vercel: "Deploy straight to Vercel as a production deployment." }[target];
        const card = h("section", { class: "card sb-deploy-card" }, h("div", { class: "sb-deploy-head" }, h("span", { class: "sb-deploy-icon", "aria-hidden": "true", text: icon }), h("div", {}, h("strong", { text: t.label }), h("p", { class: "muted small", text: intro }))));
        if (!t.connected) {
          const token = h("input", { type: "password", placeholder: `Paste a ${t.label} token`, "aria-label": `${t.label} token`, autocomplete: "off" });
          card.append(h("p", { class: "small" }, h("a", { href: help.url, target: "_blank", rel: "noopener noreferrer", text: `Create a ${t.label} token` }), h("span", { class: "muted", text: ` · ${help.text}` })),
            h("div", { class: "sb-inline" }, token, S.button("Connect", async () => { await api(`/deploy-targets/${target}`, { method: "POST", body: { token: token.value } }); Aira.toast(`${t.label} connected.`, "success"); drawDeploy(); }, { kind: "secondary" })));
          return card;
        }
        card.append(h("p", { class: "small muted" }, `Connected${t.account ? ` as ${t.account}` : ""} · token ends in ${t.last4} · `, h("button", { type: "button", class: "sb-link", text: "Disconnect", onclick: async () => { await api(`/deploy-targets/${target}`, { method: "DELETE" }); drawDeploy(); } })));
        const form = {};
        if (target === "github") {
          form.repo = h("input", { value: last.repo || (t.account ? `${t.account}/` : "") + project.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""), "aria-label": "Repository" });
          form.branch = h("input", { value: last.branch || "main", "aria-label": "Branch" });
          form.private = h("input", { type: "checkbox", checked: true });
          form.pages = h("input", { type: "checkbox" });
          card.append(h("div", { class: "sb-form-grid" }, Aira.ui.field("Repository", form.repo, "Made for you if it does not exist."), Aira.ui.field("Branch", form.branch)),
            h("label", { class: "suite-check" }, form.private, h("span", { text: "Private repository (when creating it)" })),
            h("label", { class: "suite-check" }, form.pages, h("span", { text: "Publish with GitHub Pages (needs a public repository on free plans)" })));
        }
        const result = h("div", { class: "sb-deploy-result" });
        if (last.url) result.append(h("p", { class: "small" }, "Last: ", h("a", { href: last.pages_url || last.url, target: "_blank", rel: "noopener noreferrer", text: last.pages_url || last.url })));
        card.append(h("div", { class: "btn-row" }, S.button(target === "github" ? "Push to GitHub" : `Deploy to ${t.label}`, async () => {
          const body = { target };
          if (target === "github") Object.assign(body, { repo: form.repo.value, branch: form.branch.value, private: form.private.checked, pages: form.pages.checked });
          const done = await api(`/projects/${project.id}/deploy`, { method: "POST", body });
          project = done.project;
          const url = done.deploy.url;
          Aira.toast(target === "github" ? `Pushed ${done.deploy.result.files} files to ${done.deploy.result.repo}.` : `Deployed: ${url}`, "success", { timeout: 9000 });
          result.replaceChildren(h("p", { class: "small" }, "Done: ", h("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: url })), ...(done.deploy.result.pages_error ? [h("p", { class: "small muted", text: done.deploy.result.pages_error })] : []));
        }, { kind: "primary" })), result);
        return card;
      };
      const payment = h("section", { class: "card sb-deploy-card" },
        h("div", { class: "sb-deploy-head" }, h("span", { class: "sb-deploy-icon", "aria-hidden": "true", text: "💳" }), h("div", {}, h("strong", { text: "Client payment" }), h("p", { class: "muted small", text: project.client ? `${project.client} · ${project.price_cents ? S.money(project.price_cents) : "no price set"}` : "Building this site for a client? Set the client and the price in Details." }))),
        h("div", { class: "btn-row" },
          S.button("Details", () => details(), { kind: "ghost" }),
          project.price_cents ? S.button(project.paid ? "Mark as not paid" : "Mark as paid", async () => { project = (await api(`/projects/${project.id}/paid`, { method: "POST", body: { paid: !project.paid } })).project; Aira.toast(project.paid ? "Recorded in the Business hub's revenue." : "Payment removed.", "success"); drawDeploy(); }, { kind: project.paid ? "ghost" : "secondary" }) : null,
          project.paid ? S.pill("paid") : null));
      const history = (project.deploy_history || []).length ? h("section", { class: "card sb-deploy-card" }, h("strong", { text: "Recent deploys" }), h("ul", { class: "sb-deploys" }, ...project.deploy_history.map((d) => h("li", {}, S.pill(d.status === "done" ? "completed" : "failed"), h("span", { text: ` ${d.target === "zip" ? "ZIP download" : d.target} · ${Aira.fmt.ago(d.created_at)} ` }), d.url ? h("a", { href: d.url, target: "_blank", rel: "noopener noreferrer", text: d.url }) : h("span", { class: "muted small", text: d.status === "failed" ? d.detail : "" }))))) : null;
      if (!project.version) { panelBody.replaceChildren(h("div", { class: "sb-empty-stage" }, h("strong", { text: "Build the site first" }), h("p", { class: "muted", text: "Once it has a version you can publish it, download it or deploy it." }))); return; }
      panelBody.replaceChildren(h("div", { class: "sb-deploy" }, publishCard, zipCard, targetCard("github"), targetCard("netlify"), targetCard("vercel"), payment, history));
    }

    drawHead();
    drawMessages();
    drawPanel();
    const start = pendingStart && pendingStart.id === project.id ? pendingStart : null;
    pendingStart = null;
    if (start) {
      choice.provider = start.provider; choice.model = start.model;
      comp.picker.redraw();
      build(start.prompt);
    } else if (project.running) {
      resume(project.running);
    } else {
      comp.focus();
    }
  }

  // ---------------------------------------------------------------- the Monosage frame
  Aira.appNew = Aira.appNew || {};
  Aira.appNew.sites = () => [
    { label: "New site", icon: "sites", href: "#/sites?new=1" },
    { label: "From a template", icon: "sites", href: "#/sites?template=1" },
  ];
  Aira.appSearch = Aira.appSearch || {};
  Aira.appSearch.sites = {
    placeholder: "Search your sites",
    placeholderShort: "Search",
    async suggest(query) {
      const data = await api(`/search?${new URLSearchParams({ q: query, limit: "6" })}`, { silent: true });
      return [
        ...data.projects.map((p) => ({ label: p.name, sub: [p.client, `Updated ${Aira.fmt.ago(p.updated_at)}`].filter(Boolean).join(" · "), href: `#/sites?project=${p.id}` })),
        ...data.messages.slice(0, 3).map((m) => ({ label: m.content, sub: `Asked in ${m.name}`, href: `#/sites?project=${m.project_id}` })),
      ].slice(0, 8);
    },
    run(query) { Aira.router.go(["sites"], { tab: "projects", q: query }); },
  };
})();

/* Aira — the frame around every app, like a suite of Google apps: one account, and each app with its own address
 * (/stocks, /health), name, logo, tab title and tab icon, a search box across the top, a "+ New" button above
 * its menu and the launcher (the nine dots) to every other app. Home (/home) is Today: the day across the apps and
 * a tile for every app.
 *
 * An app plugs in through two registries in its views-<id>.js, both optional:
 *   Aira.appNew[appId] = () => [{ label, icon?, href? | run?() }]
 *       what "+ New" offers; one item makes the button do it directly ("+ New idea"), several open a menu.
 *   Aira.appSearch[appId] = { placeholder?, run(query), suggest?(query) -> Promise<[{ label, sub?, href? | run?() }]> }
 *       run() shows the app's results for what was typed; suggest() fills the list under the box as the person types.
 * Without a search handler the box types into the page's own search field and offers the app's sections and the
 * other apps. DOM is built with h() and textContent only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = Aira.h;
  const $ = (id) => document.getElementById(id);
  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  const BRAND = "#176b4a";
  const HOME = { name: "Home", tagline: "Your day and all your apps", icon: "today", home: "today", path: "home" };
  Aira.apps.pseudo.home = HOME;
  const shell = (Aira.shell = { app: null });

  const productApps = () => Aira.apps.available().filter((app) => app.kind === "product" && app.present !== false && app.enabled !== false);
  const appHref = (app) => (app.id === "home" ? "/home#/today" : `/${app.path}#/${Aira.apps.homeOf(app.id)}`);
  // A link to an app: a plain click switches here (no reload); Ctrl, Shift or the middle button opens a new tab.
  const openApp = (app) => (event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button) return;
    event.preventDefault();
    if (app.id === "home") Aira.router.go(["today"]); else Aira.apps.open(app.id);
  };

  // ---------------------------------------------------------------------------------------------------------------
  // The app's name, address and tab icon
  // ---------------------------------------------------------------------------------------------------------------
  function drawFavicon(app) {
    const glyph = String(Aira.ICONS[app.icon] || Aira.ICONS.dashboard || "")
      .replace(/^<svg[^>]*>/, '<g transform="translate(4 4)" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">')
      .replace(/<\/svg>$/, "</g>");
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="${BRAND}"/>${glyph}</svg>`;
    let link = document.querySelector('link[rel="icon"]');
    if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.append(link); }
    link.href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  }

  shell.update = (app) => {
    const entry = app.id === "home" ? { ...app, ...HOME, id: "home" } : app;
    const changed = !shell.app || shell.app.id !== entry.id;
    shell.app = entry;
    document.body.dataset.app = entry.id;
    // The address names the app (/stocks#/stocks/ideas); an app the catalog does not know (the hosted organization
    // pages) lives at the root.
    const path = entry.path ? `/${entry.path}` : "/";
    if (location.pathname !== path) history.replaceState(history.state, "", path + location.search + location.hash);
    if (!changed) return;
    const brand = $("appBrand");
    if (brand) {
      brand.href = entry.path ? appHref(entry) : "#/";
      // Only the word Monosage shows: the app's own name and icon head its menu and page below the bar.
      brand.setAttribute("aria-label", `Monosage ${entry.name}: home`);
    }
    const input = $("appSearchInput");
    if (input) {
      placeSearch(entry);
      if (document.activeElement !== input) input.value = "";
      closeList();
    }
    // LinkedIn, Ollama and SerpApi only matter to Careers (and Ollama to AI): the other apps keep the bar quiet.
    const strip = $("statusStrip");
    if (strip) strip.classList.toggle("shell-quiet", !["careers", "api"].includes(entry.id));
    drawFavicon(entry);
  };

  // The search box's hint. A phone shows a short one ("Search Play"): an app's own wording would be cut after a word.
  const narrow = window.matchMedia("(max-width: 600px)");
  function placeSearch(entry) {
    const input = $("appSearchInput");
    if (!input || !entry) return;
    const handler = Aira.appSearch[entry.id];
    const long = (handler && handler.placeholder) || (entry.id === "home" ? "Search apps" : `Search in ${entry.name}`);
    const short = (handler && handler.placeholderShort) || (entry.id === "home" ? "Search apps" : `Search ${entry.name}`);
    input.placeholder = narrow.matches ? short : long;
    input.setAttribute("aria-label", long);
  }
  narrow.addEventListener("change", () => placeSearch(shell.app));

  // /stocks opens Stocks, /home opens Today; null for the root or an address that is not an app.
  shell.fromAddress = () => {
    const name = location.pathname.replace(/^\/+|\/+$/g, "").toLowerCase();
    if (!name) return null;
    if (name === "home") return Aira.views.today ? ["today"] : null;
    const app = Aira.APPS.find((entry) => entry.path === name);
    if (!app || Aira.apps.describe(app.id).enabled === false) return null;
    return [Aira.apps.homeOf(app.id)];
  };

  // The launcher's first tile: Home.
  shell.homeTile = () => {
    if (!Aira.views.today) return null;
    const node = h("a", { class: "app-tile shell-home-tile", role: "menuitem", tabindex: "-1", href: appHref({ id: "home" }), dataset: { app: "home" }, onclick: openApp({ id: "home" }) },
      h("span", { class: "app-tile-icon" }, Aira.icon("today")), h("span", { class: "app-tile-name", text: "Home" }), h("span", { class: "app-tile-tag", text: "Your day and all your apps" }));
    if (shell.app && shell.app.id === "home") node.setAttribute("aria-current", "true");
    return h("div", { class: "app-grid shell-home-row" }, node);
  };

  // ---------------------------------------------------------------------------------------------------------------
  // "+ New" above the menu, and on Home every app
  // ---------------------------------------------------------------------------------------------------------------
  function newItems(app) {
    const make = Aira.appNew[app.id];
    try { return ((typeof make === "function" ? make() : make) || []).filter((item) => item && item.label); } catch (error) { console.warn("appNew", app.id, error); return []; }
  }
  function runItem(item) {
    if (item.run) { item.run(); return; }
    if (!item.href) return;
    if (location.hash === item.href) Aira.router.render(); else location.hash = item.href;
  }
  let newMenu = null;
  function closeNewMenu(focusBack) {
    if (!newMenu) return;
    const { panel, button } = newMenu;
    newMenu = null;
    panel.remove();
    button.setAttribute("aria-expanded", "false");
    if (focusBack) button.focus();
  }
  function openNewMenu(button, items) {
    closeNewMenu(false);
    const panel = h("div", { class: "popover shell-new-menu", role: "menu", "aria-label": "Create" },
      ...items.map((item) => h("button", { type: "button", class: "menu-item", role: "menuitem", tabindex: "-1", onclick: () => { closeNewMenu(false); runItem(item); } }, Aira.icon(item.icon || "plus"), h("span", { text: item.label }))));
    panel.addEventListener("keydown", (event) => {
      const entries = [...panel.querySelectorAll('[role="menuitem"]')];
      const index = entries.indexOf(document.activeElement);
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeNewMenu(true); }
      else if (event.key === "Tab") closeNewMenu(false);
      else if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); entries[(index + (event.key === "ArrowDown" ? 1 : -1) + entries.length) % entries.length].focus(); }
    });
    button.after(panel);
    button.setAttribute("aria-expanded", "true");
    newMenu = { panel, button };
    const first = panel.querySelector('[role="menuitem"]');
    if (first) first.focus();
  }
  document.addEventListener("click", (event) => { if (newMenu && !event.target.closest(".shell-new-wrap")) closeNewMenu(false); });

  const PLUS = '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>';
  if (!Aira.ICONS.plus) Aira.ICONS.plus = PLUS;

  shell.navExtras = (app) => {
    const before = [];
    const after = [];
    const items = newItems(app);
    if (items.length) {
      const single = items.length === 1;
      const button = h("button", { type: "button", class: "shell-new", ...(single ? {} : { "aria-haspopup": "menu", "aria-expanded": "false" }), onclick: (event) => {
        event.stopPropagation();
        // Read again: who is signed in, or the app catalog, may have loaded after the menu was drawn.
        const now = newItems(app);
        const list = now.length ? now : items;
        if (list.length === 1) { if (window.innerWidth <= 900) Aira.setSidebarOpen(false); runItem(list[0]); return; }
        if (newMenu) closeNewMenu(false); else openNewMenu(button, list);
      } }, Aira.icon("plus"), h("span", { text: single ? items[0].label : "New" }));
      before.push(h("div", { class: "shell-new-wrap" }, button));
    }
    if (app.id === "home") {
      const list = productApps();
      if (list.length) {
        after.push(h("div", { class: "nav-group shell-apps", role: "group", "aria-labelledby": "navGroupApps" },
          h("span", { class: "nav-label", id: "navGroupApps", text: "Apps" }),
          ...list.map((entry) => {
            const node = h("a", { href: appHref(entry), class: "shell-app-link", dataset: { app: entry.id }, onclick: openApp(entry) }, Aira.icon(entry.icon), h("span", { text: entry.name }));
            return node;
          })));
      }
    }
    return { before, after };
  };

  // ---------------------------------------------------------------------------------------------------------------
  // A page's tabs in the left menu, like a mail app's folders (Aira.suite.tabs calls this; a page with its own tab
  // bar may too). Items: { id, label, icon?, count?, href? }; each opens its href, or #/<section>?tab=<id>.
  // ---------------------------------------------------------------------------------------------------------------
  const TAB_ICONS = [
    [/overview|home|summary|today|dashboard|markets/, "dashboard"], [/setting|setup|profile|account/, "settings"],
    [/invoice|document|file|report|statement|vault/, "documents"], [/earn|money|pay|revenue|income|sales|subscri|billing|wallet|pledge/, "studio"],
    [/client|customer|people|member|contact|friend|famil|recruit|support/, "recruiters"], [/calendar|schedul|booking|event|appointment|visit/, "interviews"],
    [/message|chat|inbox|thread|conversation/, "social"], [/analytic|stat|insight|progress|score|track|forecast|predict/, "analytics"],
    [/search|find|explore|discover|watch/, "jobs"], [/task|chore|todo|habit|routine|check/, "applications"], [/alert|reminder|notif/, "speaker"],
  ];
  const tabIcon = (item) => {
    if (item.icon && Aira.ICONS[item.icon]) return item.icon;
    const key = `${item.id} ${item.label}`.toLowerCase();
    const hit = TAB_ICONS.find(([pattern]) => pattern.test(key));
    return hit ? hit[1] : "";
  };
  const lifted = {}; // section -> { items, current }: the menu is redrawn (badges, another app) and draws them again
  shell.tabs = (section, items, current) => {
    if (!Array.isArray(items) || items.length < 2) return;
    lifted[section] = { items, current };
    drawFolders(section, items, current);
  };
  function drawFolders(section, items, current) {
    const link = document.querySelector(`#primaryNav a[data-section="${CSS.escape(section)}"]`);
    if (!link) return;
    let list = link.nextElementSibling && link.nextElementSibling.classList.contains("shell-subnav") ? link.nextElementSibling : null;
    const signature = items.map((item) => `${item.id}:${item.label}:${item.count || ""}:${item.href || ""}`).join("|");
    if (!list || list.dataset.signature !== signature) {
      const fresh = h("div", { class: "shell-subnav", dataset: { section, signature } },
        ...items.map((item) => {
          const icon = tabIcon(item);
          return h("a", { href: item.href || `#/${section}?tab=${encodeURIComponent(item.id)}`, class: "shell-sublink", dataset: { tab: item.id }, onclick: () => { if (window.innerWidth <= 900) Aira.setSidebarOpen(false); } },
            icon ? Aira.icon(icon) : h("span", { class: "shell-subdot", "aria-hidden": "true" }), h("span", { text: item.label }), item.count ? h("span", { class: "count", text: String(item.count) }) : null);
        }));
      if (list) list.replaceWith(fresh); else link.after(fresh);
      list = fresh;
    }
    for (const node of list.querySelectorAll(".shell-sublink")) node.setAttribute("aria-current", node.dataset.tab === current ? "page" : "false");
    // The app's only section: its tabs stand in for it, like a mail app's folders.
    const own = Aira.NAV.filter((item) => item.app && item.app === (Aira.NAV.find((entry) => entry.id === section) || {}).app && item.group !== "Account");
    link.classList.toggle("shell-folded", own.length === 1);
    link.classList.add("shell-has-subnav");
  }
  // After the menu is redrawn: the folders of the sections it shows; the open one only on the open page.
  shell.afterNav = () => {
    const open = Aira.router.current && Aira.router.current.path[0];
    for (const [section, entry] of Object.entries(lifted)) {
      if (!document.querySelector(`#primaryNav a[data-section="${CSS.escape(section)}"]`)) continue;
      drawFolders(section, entry.items, section === open ? entry.current : null);
    }
  };

  // ---------------------------------------------------------------------------------------------------------------
  // Search across the top
  // ---------------------------------------------------------------------------------------------------------------
  let options = [];
  let active = -1;
  let typed = 0; // the newest keystroke wins when suggestions arrive out of order
  let timer = 0; // suggestions wait for a pause in typing
  function closeList() {
    const list = $("appSearchList");
    const input = $("appSearchInput");
    if (!list || !input) return;
    list.hidden = true;
    list.replaceChildren();
    options = [];
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }
  function mark(index) {
    const input = $("appSearchInput");
    const nodes = [...$("appSearchList").querySelectorAll('[role="option"]')];
    active = index;
    nodes.forEach((node, i) => node.setAttribute("aria-selected", String(i === index)));
    if (nodes[index]) { input.setAttribute("aria-activedescendant", nodes[index].id); nodes[index].scrollIntoView({ block: "nearest" }); }
    else input.removeAttribute("aria-activedescendant");
  }
  function choose(option) {
    const input = $("appSearchInput");
    closeList();
    if (option.run) option.run(input.value.trim());
    else if (option.href && option.href.startsWith("#")) { if (location.hash === option.href) Aira.router.render(); else location.hash = option.href; }
    else if (option.app) Aira.apps.open(option.app);
    else if (option.home) Aira.router.go(["today"]);
    input.blur();
  }
  function showList(found) {
    const list = $("appSearchList");
    const input = $("appSearchInput");
    options = found.slice(0, 12);
    active = -1;
    if (!options.length) { closeList(); return; }
    list.replaceChildren(...options.map((option, index) => h("li", { id: `appSearchOpt${index}`, role: "option", "aria-selected": "false", class: "shell-search-option", onmousedown: (event) => { event.preventDefault(); choose(option); } },
      h("span", { class: "shell-search-icon", "aria-hidden": "true" }, Aira.icon(option.icon || "jobs")),
      h("span", { class: "shell-search-text" }, h("span", { text: option.label }), option.sub ? h("span", { class: "muted", text: option.sub }) : null))));
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }
  const norm = (text) => String(text || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  function builtIn(query) {
    const q = norm(query);
    const app = shell.app || {};
    const sections = (Aira.visibleNav ? Aira.visibleNav() : []).filter((item) => norm(item.label).includes(q)).map((item) => ({ label: item.label, sub: item.group === "Account" ? "Account" : app.name, icon: item.icon, href: `#/${item.id}` }));
    const others = productApps().filter((entry) => entry.id !== app.id && (norm(entry.name).includes(q) || norm(entry.tagline).includes(q))).map((entry) => ({ label: entry.name, sub: entry.tagline, icon: entry.icon, app: entry.id }));
    return [...sections, ...others];
  }
  async function suggest() {
    const input = $("appSearchInput");
    const query = input.value.trim();
    const mine = ++typed;
    if (!query) { closeList(); return; }
    const app = shell.app || {};
    const handler = Aira.appSearch[app.id];
    const own = handler && handler.run ? [{ label: `Search ${app.name} for “${query}”`, icon: "jobs", run: (q) => handler.run(q) }] : [];
    let found = [];
    if (handler && handler.suggest) {
      try { found = (await handler.suggest(query)) || []; } catch { found = []; }
      if (mine !== typed) return;
    } else if (app.id === "home") {
      // Home searches every app at once: a few of each app's records, named by their app.
      const within = (promise) => Promise.race([promise, new Promise((resolve) => { setTimeout(() => resolve([]), 2500); })]);
      const each = await Promise.all(productApps().filter((entry) => Aira.appSearch[entry.id] && Aira.appSearch[entry.id].suggest).map(async (entry) => {
        try {
          const rows = (await within(Aira.appSearch[entry.id].suggest(query))) || [];
          return rows.slice(0, 3).map((row) => ({ ...row, icon: row.icon || entry.icon, sub: row.sub ? `${entry.name} · ${row.sub}` : entry.name }));
        } catch { return []; }
      }));
      if (mine !== typed) return;
      found = each.flat();
    }
    showList([...own, ...found, ...builtIn(query)]);
  }
  // Without the app's own search: type into the page's search field, if it has one.
  function searchPage(query) {
    const field = document.querySelector("#main input[type='search'], #main input[data-search]");
    if (!field) return false;
    field.value = query;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
    field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    return true;
  }
  function submit() {
    const input = $("appSearchInput");
    const query = input.value.trim();
    clearTimeout(timer);
    typed += 1;
    if (active >= 0 && options[active]) { choose(options[active]); return; }
    if (!query) return;
    const app = shell.app || {};
    const handler = Aira.appSearch[app.id];
    closeList();
    if (handler && handler.run) { handler.run(query); input.blur(); return; }
    if (searchPage(query)) return;
    const first = builtIn(query)[0];
    if (first) { choose(first); return; }
    Aira.toast(`Nothing in ${app.name || "Monosage"} matches “${query}”.`, "info");
  }

  document.addEventListener("DOMContentLoaded", () => {
    const form = $("appSearch");
    const input = $("appSearchInput");
    if (!form || !input) return;
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(suggest, 160); });
    input.addEventListener("focus", () => { if (input.value.trim()) suggest(); });
    input.addEventListener("blur", () => setTimeout(closeList, 120));
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (!options.length) return;
        event.preventDefault();
        mark(event.key === "ArrowDown" ? (active + 1) % options.length : active <= 0 ? options.length - 1 : active - 1);
      } else if (event.key === "Escape") {
        event.stopPropagation();
        if (options.length) closeList(); else { input.value = ""; input.blur(); }
      }
    });
    form.addEventListener("submit", (event) => { event.preventDefault(); submit(); });
    const clear = $("appSearchClear");
    if (clear) clear.addEventListener("click", () => { input.value = ""; closeList(); input.focus(); });
  });
})();

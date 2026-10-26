/* Aira — the screens in the person's language (Aira.i18n).
 *
 * Every screen is written in English. This file translates what is on the page into the chosen language from one
 * dictionary per language (/static/i18n/<code>.json: English source -> translation), made from the catalog of
 * every visible string (scripts/i18n_extract.py). A source with {0}, {1} is a pattern: "{0} new headlines" matches
 * "12 new headlines" and the number is put into the translation. Text people wrote themselves (names, messages)
 * has no entry and stays as it is; mark an area translate="no" to make sure.
 *
 * A MutationObserver translates new text before the browser paints it, so pages built later need nothing special.
 * The English original is kept, so changing the language (back to English too) works without a reload. Dates and
 * numbers formatted with the browser's default locale use the chosen language. <html lang> and dir follow it, so
 * screen readers and the read-aloud voice speak the language.
 *
 * API: Aira.i18n.languages, .lang, .locale, .t(text), .set(code), .onChange(fn), .ready (a promise).
 * Adding a language: aira/i18n.py, LANGUAGES below and its JSON file.
 */
"use strict";
(() => {
  const root = typeof window !== "undefined" ? window : globalThis;
  const Aira = root.Aira || (root.Aira = { views: {} });
  const LANGUAGES = [
    { code: "en", name: "English", native: "English", locale: "en-IN", dir: "ltr" },
    { code: "te", name: "Telugu", native: "తెలుగు", locale: "te-IN", dir: "ltr" },
    { code: "hi", name: "Hindi", native: "हिन्दी", locale: "hi-IN", dir: "ltr" },
  ];
  const STORE_KEY = "aira.lang";
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "TEXTAREA", "CODE", "PRE", "NOSCRIPT", "KBD", "SAMP", "OPTION-RAW"]);
  const ATTRIBUTES = ["placeholder", "title", "aria-label", "alt"];
  const listeners = [];

  const I = (Aira.i18n = {
    languages: LANGUAGES,
    lang: "en",
    locale: "en-IN",
    dict: null,
    patterns: [],
    cache: new Map(),
    ready: Promise.resolve(),
  });

  const normalize = (code) => {
    const text = String(code || "").toLowerCase().replace("_", "-");
    if (LANGUAGES.some((l) => l.code === text)) return text;
    const base = text.split("-")[0];
    return LANGUAGES.some((l) => l.code === base) ? base : "en";
  };
  const read = () => { try { return localStorage.getItem(STORE_KEY) || ""; } catch { return ""; } };
  const write = (code) => { try { localStorage.setItem(STORE_KEY, code); } catch { /* storage unavailable */ } };

  // ---------------------------------------------------------------- dictionary and patterns
  const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  function compile(dict) {
    const patterns = [];
    for (const [source, target] of Object.entries(dict)) {
      if (!/\{\d+\}/.test(source) || typeof target !== "string") continue;
      const pieces = source.split(/(\{\d+\})/);
      const order = [];
      let regex = "^";
      let anchor = "";
      for (const piece of pieces) {
        const hole = /^\{(\d+)\}$/.exec(piece);
        if (hole) { order.push(Number(hole[1])); regex += "(.+?)"; } else { regex += escape(piece); if (piece.trim().length > anchor.length) anchor = piece.trim(); }
      }
      if (!anchor) continue;
      // A pattern with little fixed text ("{0} of {1}") would match ordinary sentences ("Out of stock"): it only
      // applies when what fills each gap has a number in it or is itself a known word ("paid").
      const letters = source.replace(/\{\d+\}/g, "").replace(/[^A-Za-z]/g, "").length;
      try { patterns.push({ re: new RegExp(`${regex}$`, "s"), order, target, anchor, size: source.length, weak: letters < 8 }); } catch { /* skip a pattern the browser cannot compile */ }
    }
    // The most specific first: a longer fixed part wins over a shorter one.
    return patterns.sort((a, b) => b.anchor.length - a.anchor.length || b.size - a.size);
  }

  function exact(text) {
    const hit = I.dict[text];
    if (typeof hit === "string") return hit;
    // "jobs" where the dictionary has "Jobs" (a word put into a sentence); Telugu and Hindi have no capitals.
    if (/^[a-z]/.test(text)) {
      const capital = I.dict[text[0].toUpperCase() + text.slice(1)];
      if (typeof capital === "string") return capital;
    }
    // "Save." vs "Save", "Open:" vs "Open": try without a trailing mark
    const match = /^(.*?)([.:!?…]+)$/s.exec(text);
    if (match && typeof I.dict[match[1]] === "string") return I.dict[match[1]] + match[2];
    return null;
  }

  function core(text) {
    const direct = exact(text);
    if (direct !== null) return direct;
    for (const pattern of I.patterns) {
      if (!text.includes(pattern.anchor)) continue;
      const found = pattern.re.exec(text);
      if (!found) continue;
      if (pattern.weak && !found.slice(1).every((value) => /\d/.test(value) || exact(value.trim()) !== null)) continue;
      return pattern.target.replace(/\{(\d+)\}/g, (_, n) => {
        const index = pattern.order.indexOf(Number(n));
        const value = index >= 0 ? found[index + 1] : "";
        // English builds plurals from pieces ("{0} card{1}" with "s"): the translation has its own plural.
        if (/^(s|es)$/.test(value)) return "";
        const inner = exact(value.trim());
        return inner !== null ? value.replace(value.trim(), inner) : value;
      });
    }
    return null;
  }

  // Translate one string; anything unknown comes back unchanged.
  I.t = (text) => {
    if (I.lang === "en" || !I.dict || typeof text !== "string") return text;
    const trimmed = text.trim();
    if (!trimmed || !/[A-Za-z]/.test(trimmed)) return text;
    let result = I.cache.get(trimmed);
    if (result === undefined) {
      result = core(trimmed);
      if (result === null && trimmed.includes(" · ")) {
        const parts = trimmed.split(" · ").map((part) => core(part) ?? part);
        result = parts.join(" · ");
        if (result === trimmed) result = null;
      }
      I.cache.set(trimmed, result);
    }
    if (result === null) return text;
    const lead = text.slice(0, text.indexOf(trimmed));
    const trail = text.slice(text.indexOf(trimmed) + trimmed.length);
    return lead + result + trail;
  };

  // ---------------------------------------------------------------- the page
  const originals = new WeakMap(); // text node -> { source, applied }
  const attrOriginals = new WeakMap(); // element -> Map(attr -> { source, applied })

  function skipped(element) {
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      if (SKIP_TAGS.has(node.tagName)) return true;
      if (node.getAttribute("translate") === "no" || node.classList.contains("notranslate") || node.isContentEditable) return true;
    }
    return false;
  }

  function textNode(node) {
    const value = node.nodeValue;
    let record = originals.get(node);
    if (!record || (value !== record.applied && value !== record.source)) {
      // new text, or the app changed it since we translated it
      record = { source: value, applied: value };
      originals.set(node, record);
    }
    const target = I.lang === "en" ? record.source : I.t(record.source);
    if (target !== value) { record.applied = target; node.nodeValue = target; } else record.applied = value;
  }

  function attributes(element) {
    let records = attrOriginals.get(element);
    for (const name of ATTRIBUTES) {
      if (!element.hasAttribute(name)) continue;
      const value = element.getAttribute(name);
      if (!records) { records = new Map(); attrOriginals.set(element, records); }
      let record = records.get(name);
      if (!record || (value !== record.applied && value !== record.source)) { record = { source: value, applied: value }; records.set(name, record); }
      const target = I.lang === "en" ? record.source : I.t(record.source);
      if (target !== value) { record.applied = target; element.setAttribute(name, target); }
    }
  }

  function walk(rootNode) {
    if (!rootNode) return;
    if (rootNode.nodeType === 3) {
      if (rootNode.parentElement && !skipped(rootNode.parentElement) && rootNode.nodeValue.trim()) textNode(rootNode);
      return;
    }
    if (rootNode.nodeType !== 1 || skipped(rootNode)) return;
    attributes(rootNode);
    const walker = document.createTreeWalker(rootNode, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (node) => {
        if (node.nodeType === 1) return SKIP_TAGS.has(node.tagName) || node.getAttribute("translate") === "no" || node.classList.contains("notranslate") || node.isContentEditable ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
        return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      },
    });
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.nodeType === 3) textNode(node); else attributes(node);
    }
  }

  let observer = null;
  function observe() {
    if (observer || typeof MutationObserver === "undefined") return;
    observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === "childList") record.addedNodes.forEach(walk);
        else if (record.type === "characterData") walk(record.target);
        else if (record.type === "attributes" && record.target.nodeType === 1 && !skipped(record.target)) attributes(record.target);
      }
    });
    observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRIBUTES });
  }

  // The tab title: "Jobs · Careers · Aira"
  let titleSource = "";
  function retitle() {
    const current = document.title;
    if (current !== I._appliedTitle) titleSource = current;
    const target = I.lang === "en" ? titleSource : titleSource.split(" · ").map((part) => I.t(part)).join(" · ");
    I._appliedTitle = target;
    if (target !== current) document.title = target;
  }

  // ---------------------------------------------------------------- dates and numbers in the chosen language
  const useLocale = (locales) => (locales === undefined || (Array.isArray(locales) && !locales.length) ? I.locale : locales);
  for (const [proto, names] of [[Date.prototype, ["toLocaleString", "toLocaleDateString", "toLocaleTimeString"]], [Number.prototype, ["toLocaleString"]]]) {
    for (const name of names) {
      const original = proto[name];
      proto[name] = function localized(locales, options) { return original.call(this, useLocale(locales), options); };
    }
  }

  // ---------------------------------------------------------------- choosing a language
  async function load(code) {
    if (code === "en") return null;
    const response = await fetch(`/static/i18n/${code}.json`, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`No translation for ${code}`);
    return response.json();
  }

  I.set = async (code, { save = true } = {}) => {
    const lang = normalize(code);
    const info = LANGUAGES.find((l) => l.code === lang) || LANGUAGES[0];
    let dict = null;
    try { dict = await load(lang); } catch (error) { console.warn(error); if (lang !== "en") return I.set("en", { save }); }
    I.lang = lang;
    I.locale = info.locale;
    I.dict = dict;
    I.patterns = dict ? compile(dict) : [];
    I.cache = new Map();
    document.documentElement.lang = lang;
    document.documentElement.dir = info.dir;
    write(lang);
    walk(document.body);
    retitle();
    renderPickers();
    for (const fn of listeners) { try { fn(lang, { save }); } catch (error) { console.warn(error); } }
    return lang;
  };
  I.onChange = (fn) => listeners.push(fn);
  I.native = (code) => (LANGUAGES.find((l) => l.code === code) || LANGUAGES[0]).native;

  // A small language button: in the top bar (#langBtn), on the sign-in screen and on public pages (#langCorner).
  function picker(button) {
    const close = () => { const menu = document.getElementById("langMenu"); if (menu) menu.remove(); document.removeEventListener("click", outside, true); };
    const outside = (event) => { const menu = document.getElementById("langMenu"); if (menu && !menu.contains(event.target) && event.target !== button) close(); };
    const existing = document.getElementById("langMenu");
    if (existing) { close(); return; }
    const menu = document.createElement("div");
    menu.id = "langMenu";
    menu.className = "lang-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("translate", "no");
    for (const language of LANGUAGES) {
      const item = document.createElement("button");
      item.type = "button";
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("aria-checked", String(language.code === I.lang));
      item.lang = language.code;
      item.textContent = language.code === "en" ? "English" : `${language.native} (${language.name})`;
      item.addEventListener("click", async () => { close(); await I.set(language.code); button.focus(); });
      menu.append(item);
    }
    const rect = button.getBoundingClientRect();
    menu.style.top = `${Math.round(rect.bottom + 6)}px`;
    menu.style.right = `${Math.max(8, Math.round(window.innerWidth - rect.right))}px`;
    document.body.append(menu);
    menu.querySelector("[aria-checked='true']")?.focus();
    setTimeout(() => document.addEventListener("click", outside, true), 0);
    menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { close(); button.focus(); } });
  }

  function renderPickers() {
    for (const id of ["langBtn", "langCornerBtn"]) {
      const button = document.getElementById(id);
      if (!button) continue;
      const label = button.querySelector(".lang-name");
      if (label) label.textContent = I.native(I.lang);
      button.setAttribute("aria-label", `Language: ${I.native(I.lang)}`);
    }
  }

  function mountPickers() {
    let corner = document.getElementById("langCorner");
    if (!corner) {
      corner = document.createElement("div");
      corner.id = "langCorner";
      corner.className = "lang-corner";
      corner.setAttribute("translate", "no");
      corner.innerHTML = '<button type="button" id="langCornerBtn" class="lang-btn"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/></svg><span class="lang-name"></span></button>';
      document.body.append(corner);
    }
    for (const id of ["langBtn", "langCornerBtn"]) {
      const button = document.getElementById(id);
      if (button && !button.dataset.bound) { button.dataset.bound = "1"; button.addEventListener("click", () => picker(button)); }
    }
    renderPickers();
  }

  // ---------------------------------------------------------------- start
  const initial = normalize(read() || (navigator.language || "en"));
  I.ready = (async () => {
    const start = () => { observe(); mountPickers(); };
    if (document.body) start(); else document.addEventListener("DOMContentLoaded", start, { once: true });
    if (initial !== "en") await I.set(initial, { save: false });
    else { document.documentElement.lang = "en"; renderPickers(); }
    if (typeof MutationObserver !== "undefined") {
      const title = document.querySelector("title");
      if (title) new MutationObserver(retitle).observe(title, { childList: true, characterData: true, subtree: true });
    }
  })();
})();

/* Aira — Stocks (#/stocks): markets, each stock's daily log, predictions with their track record, practice and real
 * portfolios, watchlists, price alerts and earning from published stock ideas.
 *
 * Tabs (?tab=): markets (indices, top gainers and losers, most active, 52-week highs and lows, every stock), watchlist,
 * portfolio (&kind=practice|real, plus the practice league), predictions (&symbol=), ideas (earn: the analyst page,
 * ideas, subscribers and payments) and alerts. A stock's own page is #/stocks?symbol=AAPL (&range=1m|3m|6m|1y|2y).
 * Everything runs through /api/stocks/* (aira/suite/stocks). Rises and falls are shown with + and - signs and
 * arrows, never by colour alone. DOM is built with h() and text only; charts are drawn as SVG here.
 *
 * In the Aira frame (shell.js): the tabs are the left menu's folders and the page is named after the open one (a
 * stock's page after the stock). "+ New" offers a practice trade, a price alert and a prediction, and for adults a
 * real trade and a stock idea; each asks with ?new=<kind> (&symbol= from a stock's page), which the view takes out of
 * the address and answers with the tab's own form once the tab is drawn. The search across the top suggests stocks
 * (GET /api/stocks/search, marked when they are on a watchlist) and Enter lists every match (#/stocks?q=).
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("stocks", path, opts);
  const enc = encodeURIComponent;
  const put = (node, ...children) => node.append(...children.filter(Boolean));
  const go = (query) => Aira.router.go(["stocks"], query);

  // A few glyphs the shared set lacks, for the menu's folders, "+ New" and the search list (added once, never replaced).
  const GLYPHS = {
    stEye: '<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    stWallet: '<svg viewBox="0 0 24 24"><path d="M17 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v2"/><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M16 13.5h2"/></svg>',
    stBell: '<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>',
    stIdea: '<svg viewBox="0 0 24 24"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/></svg>',
    stTrade: '<svg viewBox="0 0 24 24"><path d="M7 4v16M3 8l4-4 4 4"/><path d="M17 20V4M13 16l4 4 4-4"/></svg>',
    stBack: '<svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>',
  };
  for (const [name, glyph] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = glyph;

  // Real money (real holdings, ideas, subscribers) is for adults; the server refuses the rest anyway, this keeps the
  // offers away. Until the profile is known, assume an adult (the tab then explains a refusal).
  const grownUp = () => { const me = Aira.life && Aira.life.me; return !me || !me.age_group || ["adult", "senior"].includes(me.age_group); };
  // What "+ New" asked for (?new=), answered by the drawn tab through hooks[kind].
  let pending = null;
  const hooks = {};
  // List answers for the search box, kept for a few seconds (and dropped whenever the page is drawn again).
  const answers = new Map();
  function cached(path) {
    const hit = answers.get(path);
    if (hit && Date.now() - hit.at < 8000) return hit.promise;
    const promise = api(path, { silent: true }).catch(() => null);
    answers.set(path, { at: Date.now(), promise });
    return promise;
  }

  // ---------------------------------------------------------------- numbers
  const SYMBOLS = { USD: "$", EUR: "€", GBP: "£", INR: "₹", CAD: "CA$", AUD: "A$", SGD: "S$", AED: "AED ", JPY: "¥" };
  const num = (value, digits = 2) => Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  function money(cents, currency) {
    const code = currency || "USD";
    const symbol = SYMBOLS[code] !== undefined ? SYMBOLS[code] : `${code} `;
    const value = Number(cents || 0) / 100;
    const locale = code === "INR" ? "en-IN" : undefined; // ₹10,00,000.00 reads the Indian way
    if (code === "JPY") return `${value < 0 ? "-" : ""}${symbol}${Math.abs(Math.round(value)).toLocaleString()}`;
    return `${value < 0 ? "-" : ""}${symbol}${Math.abs(value).toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  const priceOf = (cents, quote) => (quote && quote.index ? num(cents / 100) : money(cents, quote ? quote.currency : "USD"));
  const signedMoney = (cents, currency) => `${cents > 0 ? "+" : cents < 0 ? "-" : ""}${money(Math.abs(cents || 0), currency)}`;
  const signedPct = (value) => (value === null || value === undefined ? "n/a" : `${value > 0 ? "+" : ""}${Number(value).toFixed(2)}%`);
  const arrow = (value) => (value > 0 ? "▲" : value < 0 ? "▼" : "■");
  const toneOf = (value) => (value > 0 ? "st-up" : value < 0 ? "st-down" : "st-flat");
  const qty = (value) => Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 6 });
  const volume = (value) => (value >= 1e9 ? `${(value / 1e9).toFixed(2)}B` : value >= 1e6 ? `${(value / 1e6).toFixed(2)}M` : value >= 1e3 ? `${(value / 1e3).toFixed(1)}K` : String(value || 0));
  // A rise or a fall: an arrow and a sign, with a colour only as a help.
  const change = (value, label) => h("span", { class: `st-chg ${toneOf(value)}` }, h("span", { class: "st-arrow", "aria-hidden": "true", text: arrow(value) }), h("span", { text: label }));
  const quoteChange = (q) => change(q.change_pct, `${q.index ? `${q.change_cents > 0 ? "+" : ""}${num(q.change_cents / 100)}` : signedMoney(q.change_cents, q.currency)} (${signedPct(q.change_pct)})`);
  const pill = (text, tone = "") => h("span", { class: `suite-pill as-written ${tone}`, text });
  const DISCLAIMER = "Not financial advice. Forecasts are estimates and are often wrong. Monosage never buys or sells real shares.";
  const disclaimer = (text) => h("p", { class: "st-disclaimer", role: "note" }, h("strong", { text: "Please note: " }), h("span", { text: text || DISCLAIMER }));
  const stockLink = (symbol, label) => h("a", { href: `#/stocks?symbol=${enc(symbol)}`, class: "st-link", text: label || symbol });

  // ---------------------------------------------------------------- SVG chart
  const NS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs = {}, ...children) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) if (value !== undefined && value !== null) node.setAttribute(key, String(value));
    for (const child of children) if (child) node.append(child);
    return node;
  }
  /* lineChart({dates, lines: [{values, cls, name}], future: [{date?, price, low, high}], format, label})
   * The first line is the price; arrow keys or the pointer move a cursor whose values are read out below. */
  function lineChart({ dates, lines, future = [], format = (v) => num(v), label = "Price chart", height = 260 }) {
    const W = 720, H = height, L = 6, R = 70, T = 12, B = 26;
    const total = dates.length + future.length;
    const values = [...lines.flatMap((line) => line.values), ...future.flatMap((f) => [f.low, f.high])].filter((v) => v !== null && v !== undefined);
    if (!dates.length || !values.length) return Aira.ui.empty("No prices to draw", "");
    let min = Math.min(...values), max = Math.max(...values);
    if (min === max) { min -= 1; max += 1; }
    const pad = (max - min) * 0.06;
    min -= pad; max += pad;
    const x = (i) => L + (total <= 1 ? 0 : (i / (total - 1)) * (W - L - R));
    const y = (v) => T + (1 - (v - min) / (max - min)) * (H - T - B);
    const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "st-svg", role: "img", "aria-label": label, preserveAspectRatio: "none" });
    for (let k = 0; k <= 4; k += 1) {
      const v = min + ((max - min) * k) / 4;
      chart.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "st-grid" }), svg("text", { x: W - R + 6, y: y(v) + 4, class: "st-axis" }, document.createTextNode(format(v))));
    }
    const ticks = [0, Math.floor((dates.length - 1) / 2), dates.length - 1];
    for (const [n, i] of ticks.entries()) chart.append(svg("text", { x: x(i), y: H - 6, class: "st-axis", "text-anchor": n === 0 ? "start" : n === 1 ? "middle" : "end" }, document.createTextNode(Aira.fmt.date(dates[i]))));
    if (future.length) {
      const start = dates.length - 1;
      const last = lines[0].values[start];
      const top = [[start, last], ...future.map((f, i) => [start + 1 + i, f.high])];
      const bottom = [[start, last], ...future.map((f, i) => [start + 1 + i, f.low])].reverse();
      chart.append(svg("polygon", { class: "st-band", points: [...top, ...bottom].map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ") }));
      chart.append(svg("path", { class: "st-line st-forecast", d: [[start, last], ...future.map((f, i) => [start + 1 + i, f.price])].map(([i, v], n) => `${n ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ") }));
      chart.append(svg("line", { x1: x(start), x2: x(start), y1: T, y2: H - B, class: "st-today" }));
    }
    for (const line of [...lines].reverse()) {
      let d = "", pen = false;
      line.values.forEach((v, i) => { if (v === null || v === undefined) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`; pen = true; });
      if (d) chart.append(svg("path", { d, class: `st-line ${line.cls || ""}` }));
    }
    const cursor = svg("line", { y1: T, y2: H - B, class: "st-cursor", visibility: "hidden" });
    const dot = svg("circle", { r: 4, class: "st-dot", visibility: "hidden" });
    chart.append(cursor, dot);
    const readout = h("p", { class: "st-readout", "aria-live": "polite", text: "Move over the chart, or focus it and use the arrow keys, to read a day." });
    const legend = h("div", { class: "st-legend" }, ...lines.map((line) => h("span", { class: `st-key ${line.cls || ""}` }, h("span", { class: "st-swatch", "aria-hidden": "true" }), h("span", { text: line.name }))), future.length ? h("span", { class: "st-key st-forecast" }, h("span", { class: "st-swatch", "aria-hidden": "true" }), h("span", { text: "Forecast with its 80% band" })) : null);
    let at = dates.length - 1;
    const show = (i) => {
      at = Math.max(0, Math.min(total - 1, i));
      const isFuture = at >= dates.length;
      const value = isFuture ? future[at - dates.length].price : lines[0].values[at];
      if (value === null || value === undefined) return;
      cursor.setAttribute("x1", x(at)); cursor.setAttribute("x2", x(at)); cursor.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x(at)); dot.setAttribute("cy", y(value)); dot.setAttribute("visibility", "visible");
      if (isFuture) {
        const f = future[at - dates.length];
        readout.textContent = `Forecast, trading day ${at - dates.length + 1}: ${format(f.price)} (80% band ${format(f.low)} to ${format(f.high)})`;
      } else {
        const extra = lines.slice(1).map((line) => (line.values[at] === null || line.values[at] === undefined ? "" : `${line.name} ${format(line.values[at])}`)).filter(Boolean).join(" · ");
        readout.textContent = `${Aira.fmt.date(dates[at])}: ${format(value)}${extra ? ` · ${extra}` : ""}`;
      }
    };
    const box = h("div", { class: "st-chart", tabindex: "0", "aria-label": `${label}. Use the left and right arrow keys to read each day.` }, chart);
    box.addEventListener("pointermove", (event) => { const rect = chart.getBoundingClientRect(); const ratio = (event.clientX - rect.left) / rect.width; show(Math.round(((ratio * W - L) / (W - L - R)) * (total - 1))); });
    box.addEventListener("keydown", (event) => {
      const step = { ArrowLeft: -1, ArrowRight: 1, PageUp: -20, PageDown: 20 }[event.key];
      if (step) { event.preventDefault(); show(at + step); }
      if (event.key === "Home") { event.preventDefault(); show(0); }
      if (event.key === "End") { event.preventDefault(); show(total - 1); }
    });
    box.addEventListener("focus", () => show(at));
    return h("div", { class: "st-chart-wrap" }, box, legend, readout);
  }

  // ---------------------------------------------------------------- search
  function searchBox(onPick, { placeholder = "Find a stock: Apple, AAPL, Reliance, TCS.NS", label = "Find a stock" } = {}) {
    const input = h("input", { type: "search", placeholder, "aria-label": label, autocomplete: "off", maxlength: 40, class: "st-search-input" });
    const list = h("ul", { class: "st-results", role: "listbox", "aria-label": "Matching stocks", hidden: true });
    let timer = 0, seq = 0;
    const pick = (symbol) => { list.hidden = true; input.value = ""; onPick(symbol); };
    const run = async () => {
      const query = input.value.trim();
      if (!query) { list.hidden = true; return; }
      const mine = (seq += 1);
      let data;
      try { data = await api(`/search?q=${enc(query)}`, { silent: true }); } catch (error) { Aira.reportError(error); return; }
      if (mine !== seq) return;
      list.replaceChildren(...(data.results.length ? data.results.map((r) => h("li", {}, h("button", { type: "button", class: "st-result", onclick: () => pick(r.symbol) }, h("strong", { text: r.symbol }), h("span", { class: "muted", text: ` ${r.name}${r.index ? " (index)" : ""}` })))) : [h("li", { class: "muted st-none", text: `Nothing found for "${query}". Indian shares end in .NS, for example TCS.NS.` })]));
      list.hidden = false;
    };
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 250); });
    input.addEventListener("keydown", async (event) => {
      if (event.key === "Enter") { event.preventDefault(); clearTimeout(timer); await run(); const first = list.querySelector("button"); if (first) first.click(); }
      if (event.key === "ArrowDown") { const first = list.querySelector("button"); if (first) { event.preventDefault(); first.focus(); } }
      if (event.key === "Escape") list.hidden = true;
    });
    list.addEventListener("keydown", (event) => {
      const buttons = [...list.querySelectorAll("button")];
      const index = buttons.indexOf(document.activeElement);
      if (event.key === "ArrowDown" && index < buttons.length - 1) { event.preventDefault(); buttons[index + 1].focus(); }
      if (event.key === "ArrowUp") { event.preventDefault(); (index > 0 ? buttons[index - 1] : input).focus(); }
      if (event.key === "Escape") { list.hidden = true; input.focus(); }
    });
    return h("div", { class: "st-search" }, input, list);
  }

  // ---------------------------------------------------------------- forms shared by several screens
  /* Money is typed and sent as text ("182.50" as `price`, not `price_cents`): the server reads it, so a typo such
   * as "abc" comes back as an error in the dialog instead of being saved as 0. */
  function moneyAsText(fields, values = {}) {
    const out = { ...values };
    const list = fields.map((field) => {
      if (field.type !== "money") return field;
      const name = field.name.replace(/_cents$/, "");
      const cents = values[field.name] !== undefined ? values[field.name] : field.value;
      delete out[field.name];
      if (cents !== undefined && cents !== null && cents !== "") out[name] = S.fromCents(cents);
      return { ...field, name, type: "text", placeholder: field.placeholder || "0.00", value: undefined };
    });
    return { fields: list, values: out };
  }
  const edit = (options) => S.edit({ ...options, ...moneyAsText(options.fields, options.values || {}) });

  async function practiceTrade(symbol, side, rerender, quote) {
    const values = { symbol: symbol || "", side: side || "buy" };
    await edit({
      title: side === "sell" ? "Sell with practice money" : "Buy with practice money",
      fields: [
        { name: "symbol", label: "Stock symbol", required: true, placeholder: "AAPL", hint: quote ? `Latest price ${priceOf(quote.price_cents, quote)}. Trades fill at the latest price.` : "Trades fill at the latest price." },
        { name: "side", label: "Buy or sell", type: "select", options: [["buy", "Buy"], ["sell", "Sell"]] },
        { name: "quantity", label: "Number of shares", type: "number", min: 0, step: "any", hint: "Part of a share is fine (for example 0.5)." },
        { name: "amount_cents", label: "Or an amount of money to buy for", type: "money", placeholder: "1000.00", hint: "Leave the number of shares empty to buy as many as this amount pays for." },
      ],
      values,
      submitLabel: side === "sell" ? "Sell" : "Buy",
      onSave: async (form) => {
        const body = { ...form };
        if (body.quantity !== null && body.quantity !== "") delete body.amount;
        else if (!body.amount) throw new Error("Enter the number of shares or an amount of money.");
        const result = await api("/practice/trade", { method: "POST", body });
        Aira.toast(result.message, "success");
        if (rerender) rerender();
      },
    });
  }

  async function realTrade(defaults, rerender, existing) {
    await edit({
      title: existing ? "Change this trade" : "Record a real trade",
      fields: [
        { name: "kind", label: "What happened", type: "select", options: [["buy", "I bought shares"], ["sell", "I sold shares"], ["dividend", "I received a dividend"]] },
        { name: "symbol", label: "Stock symbol", required: true, placeholder: "AAPL or RELIANCE.NS", hint: existing ? "The stock of a trade cannot change; delete it and record a new one." : "" },
        { name: "trade_date", label: "Date of the trade", type: "date", required: true, hint: existing ? "" : "The day you bought or sold, from your broker's statement. Today's change counts from this date." },
        { name: "quantity", label: "Number of shares (for buys and sells)", type: "number", min: 0, step: "any" },
        { name: "price_cents", label: "Price per share", type: "money" },
        { name: "fees_cents", label: "Fees and taxes (optional)", type: "money" },
        { name: "amount_cents", label: "Dividend received (for dividends)", type: "money" },
        { name: "note", label: "Note (optional)", placeholder: "Broker, reason, ..." },
      ],
      values: existing || { kind: "buy", ...defaults }, // no date filled in: an old holding is not "bought today"
      submitLabel: existing ? "Save" : "Record",
      onSave: async (form) => {
        const body = { ...form };
        if (body.kind === "dividend") { delete body.quantity; delete body.price; delete body.fees; } else delete body.amount;
        if (existing) { delete body.symbol; await api(`/real/transactions/${existing.id}`, { method: "PATCH", body }); } else await api("/real/transactions", { method: "POST", body });
        Aira.toast(existing ? "Trade changed." : "Recorded. Monosage did not trade anything: this is your record.", "success");
        if (rerender) rerender();
      },
    });
  }

  async function newAlert(symbol, rerender, quote) {
    await edit({
      title: symbol ? `Price alert for ${symbol}` : "New price alert",
      fields: [
        { name: "symbol", label: "Stock symbol", required: true, placeholder: "AAPL" },
        { name: "kind", label: "Tell me when", type: "select", options: [["above", "The price rises above"], ["below", "The price falls below"], ["move", "It moves a lot in one day"]] },
        { name: "price_cents", label: "Price (for above or below)", type: "money", hint: quote ? `Now ${priceOf(quote.price_cents, quote)}.` : "" },
        { name: "move_pct", label: "Daily move in % (for a big move)", type: "number", min: 0.1, max: 50, step: 0.1, placeholder: "3" },
        { name: "note", label: "Note (optional)" },
      ],
      values: { symbol: symbol || "", kind: "above" },
      submitLabel: "Create alert",
      onSave: async (form) => {
        const body = { ...form };
        if (body.kind === "move") delete body.price; else delete body.move_pct;
        const result = await api("/alerts", { method: "POST", body });
        Aira.toast(`Alert set: ${result.alert.text}.`, "success");
        if (rerender) rerender();
      },
    });
  }

  async function addToWatchlist(symbol, rerender, lists) {
    let listId = "";
    if (lists && lists.length > 1) {
      const chosen = await new Promise((resolve) => {
        const select = Aira.ui.select(lists.map((l) => [l.id, l.name]), lists[0].id, { "aria-label": "Watchlist" });
        Aira.modal({ title: `Add ${symbol} to a watchlist`, body: Aira.ui.field("Watchlist", select), actions: [{ label: "Cancel", kind: "ghost", onClick: () => resolve(null) }, { label: "Add", kind: "primary", onClick: () => resolve(select.value) }], onClose: () => resolve(null) });
      });
      if (!chosen) return;
      listId = chosen;
    }
    const result = await api("/watchlist", { method: "POST", body: { symbol, list_id: listId } });
    Aira.toast(`${result.item.symbol} is on ${result.list.name}.`, "success");
    if (rerender) rerender();
  }

  // ---------------------------------------------------------------- a stock's page
  async function stockPage(body, route, retitle) {
    const symbol = route.query.symbol;
    const range = ["1m", "3m", "6m", "1y", "2y"].includes(route.query.range) ? route.query.range : "1y";
    const data = await api(`/stock?symbol=${enc(symbol)}&range=${range}`);
    const q = data.quote;
    const rerender = () => Aira.router.render();
    const fmt = (v) => (q.index ? num(v) : money(Math.round(v * 100), q.currency));
    // The page is named after the stock, with its symbol, exchange and currency under the name.
    retitle(q.name, [q.symbol, q.exchange, q.index ? "Index" : q.currency].filter(Boolean).join(" · "));
    const head = h("section", { class: "card st-hero" },
      h("div", { class: "st-hero-main" },
        h("p", { class: "st-hero-price" }, h("strong", { text: priceOf(q.price_cents, q) }), " ", quoteChange(q)),
        h("p", { class: "muted small", text: `${q.stale ? "Saved prices (the market data could not be reached). " : ""}As of ${S.when(q.market_time)}. Prices may be delayed.` })),
      h("div", { class: "btn-row st-hero-actions" },
        data.watching.length ? pill(`On ${data.watching.map((w) => w.name).join(", ")}`, "good") : S.button("Add to watchlist", () => addToWatchlist(q.symbol, rerender)),
        S.button("Set an alert", () => newAlert(q.symbol, rerender, q), { kind: "secondary" }),
        q.index ? null : S.button("Practice buy", () => (data.practice_currency_ready ? practiceTrade(q.symbol, "buy", rerender, q) : go({ tab: "portfolio", kind: "practice" })), { kind: "secondary" }),
        q.index || !data.positions.some((p) => p.kind === "practice") ? null : S.button("Practice sell", () => practiceTrade(q.symbol, "sell", rerender, q), { kind: "secondary" }),
        !q.index && data.adult ? S.button("Record a real trade", () => realTrade({ symbol: q.symbol, price_cents: q.price_cents }, rerender), { kind: "ghost" }) : null,
        S.button("See the prediction", () => go({ tab: "predictions", symbol: q.symbol }), { kind: "ghost" })));
    const ranges = h("div", { class: "st-ranges", role: "group", "aria-label": "Chart range" }, ...[["1m", "1M"], ["3m", "3M"], ["6m", "6M"], ["1y", "1Y"], ["2y", "2Y"]].map(([id, text]) => h("button", { type: "button", class: "btn sm secondary", "aria-pressed": String(id === range), "aria-label": `Show ${text}`, text, onclick: () => go({ symbol: q.symbol, range: id }) })));
    const toggles = { sma20: false, sma50: true, sma200: false };
    const chartHolder = h("div", {});
    const drawChart = () => {
      const lines = [{ values: data.chart.close, cls: "st-price", name: "Close" }];
      if (toggles.sma20) lines.push({ values: data.chart.sma20, cls: "st-sma20", name: "20-day average" });
      if (toggles.sma50) lines.push({ values: data.chart.sma50, cls: "st-sma50", name: "50-day average" });
      if (toggles.sma200) lines.push({ values: data.chart.sma200, cls: "st-sma200", name: "200-day average" });
      chartHolder.replaceChildren(lineChart({ dates: data.chart.dates, lines, format: fmt, label: `${q.name} closing prices, ${range.toUpperCase()}` }));
    };
    const toggleRow = h("div", { class: "btn-row st-toggles" }, ...[["sma20", "20-day average"], ["sma50", "50-day average"], ["sma200", "200-day average"]].map(([key, text]) => {
      const box = h("input", { type: "checkbox", checked: toggles[key] });
      box.addEventListener("change", () => { toggles[key] = box.checked; drawChart(); });
      return h("label", { class: "suite-check" }, box, h("span", { text }));
    }));
    drawChart();
    const s = data.summary;
    const dayTable = (rows, empty) => S.table({
      columns: [
        { label: "Day", render: (r) => S.day(r.date) },
        { label: "Change", render: (r) => change(r.change_pct, signedPct(r.change_pct)), num: true },
        { label: "Close", render: (r) => priceOf(r.close_cents, q), num: true },
      ],
      rows, empty,
    });
    const logTable = h("div", {});
    let shown = 30;
    const drawLog = () => {
      logTable.replaceChildren(S.table({
        caption: `Daily log of ${q.symbol}`,
        columns: [
          { label: "Day", render: (r) => S.day(r.date) },
          { label: "Open", render: (r) => priceOf(r.open_cents, q), num: true },
          { label: "High", render: (r) => priceOf(r.high_cents, q), num: true },
          { label: "Low", render: (r) => priceOf(r.low_cents, q), num: true },
          { label: "Close", render: (r) => priceOf(r.close_cents, q), num: true },
          { label: "Change", render: (r) => change(r.change_pct, `${q.index ? num(r.change_cents / 100) : signedMoney(r.change_cents, q.currency)} (${signedPct(r.change_pct)})`), num: true },
          { label: "Volume", render: (r) => volume(r.volume), num: true },
        ],
        rows: data.log.slice(0, shown),
        empty: "No trading days in this range.",
      }), shown < data.log.length ? h("div", { class: "btn-row" }, S.button(`Show ${Math.min(30, data.log.length - shown)} more days`, () => { shown += 30; drawLog(); }, { kind: "secondary sm" }), h("span", { class: "muted small", text: `${shown} of ${data.log.length} days` })) : null);
    };
    drawLog();
    put(body,
      h("div", { class: "btn-row" }, h("a", { class: "btn ghost sm st-back", href: `#/stocks?tab=markets&market=${q.market === "in" ? "in" : "us"}` }, Aira.icon("stBack"), h("span", { text: q.market === "in" ? "India (NSE) market" : "US market" }))),
      head,
      S.section("Chart", ranges, chartHolder, toggleRow),
      S.kpis([
        { label: "Open", value: priceOf(q.open_cents, q) },
        { label: "Day high", value: priceOf(q.day_high_cents, q) },
        { label: "Day low", value: priceOf(q.day_low_cents, q) },
        { label: "Previous close", value: priceOf(q.previous_close_cents, q) },
        { label: "Volume", value: volume(q.volume) },
        { label: "52-week high", value: priceOf(q.high52_cents, q) },
        { label: "52-week low", value: priceOf(q.low52_cents, q) },
      ]),
      S.section(`The ${range.toUpperCase()} in numbers`,
        S.kpis([
          { label: "Change over the period", value: signedPct(s.return_pct), tone: s.return_pct > 0 ? "good" : s.return_pct < 0 ? "bad" : "", hint: s.return_pct > 0 ? "▲ up" : s.return_pct < 0 ? "▼ down" : "" },
          s.high ? { label: "Highest price", value: priceOf(s.high.price_cents, q), hint: S.day(s.high.date) } : null,
          s.low ? { label: "Lowest price", value: priceOf(s.low.price_cents, q), hint: S.day(s.low.date) } : null,
          { label: "Days up / down", value: `${s.up_days || 0} / ${s.down_days || 0}`, hint: `of ${s.days} trading days` },
        ]),
        S.grid(h("div", {}, h("h3", { class: "suite-h3", text: "Best days (top gains)" }), dayTable(s.best, "No day closed higher.")), h("div", {}, h("h3", { class: "suite-h3", text: "Worst days (biggest falls)" }), dayTable(s.worst, "No day closed lower.")))),
      data.positions.length ? S.section("Your position", S.table({
        columns: [
          { label: "Portfolio", render: (p) => (p.kind === "practice" ? "Practice" : "My holdings") },
          { label: "Shares", render: (p) => qty(p.quantity), num: true },
          { label: "Average cost", render: (p) => money(p.average_cents, q.currency), num: true },
          { label: "Value", render: (p) => money(p.value_cents, q.currency), num: true },
          { label: "Profit or loss", render: (p) => change(p.unrealised_cents, `${signedMoney(p.unrealised_cents, q.currency)} (${signedPct(p.unrealised_pct)})`), num: true },
        ],
        rows: data.positions,
      })) : null,
      data.alerts.length ? S.section("Your alerts on this stock", h("ul", { class: "life-lines" }, ...data.alerts.map((a) => h("li", { class: "life-line" }, h("span", { class: "life-line-text", text: a.text }), pill(a.active ? "Waiting" : a.triggered ? "Fired" : "Off", a.active ? "info" : a.triggered ? "warn" : ""))))) : null,
      S.section("Daily log", h("p", { class: "muted", text: "Every trading day: where it opened, its high and low, where it closed and how much it moved." }), h("div", { class: "btn-row" }, S.button("Export CSV", () => Aira.download(`/api/stocks/export/log?symbol=${enc(q.symbol)}&range=${range}`, `${q.symbol}-daily-log.csv`), { kind: "ghost sm" })), logTable),
      disclaimer(data.disclaimer));
  }

  // ---------------------------------------------------------------- markets
  function moverList(rows, empty, value) {
    if (!rows.length) return h("p", { class: "muted", text: empty });
    return h("ol", { class: "st-movers" }, ...rows.map((r) => h("li", {}, h("a", { href: `#/stocks?symbol=${enc(r.symbol)}`, class: "st-mover" }, h("span", { class: "st-mover-name" }, h("strong", { text: r.symbol.replace(/\.NS$/, "") }), h("span", { class: "muted small", text: r.name })), h("span", { class: "st-mover-num" }, value(r))))));
  }

  async function marketsTab(body, route) {
    const marketId = route.query.market === "in" ? "in" : "us";
    const [data, mine] = await Promise.all([api(`/markets?market=${marketId}`), api("/overview", { silent: true }).catch(() => null)]);
    const switcher = h("div", { class: "st-ranges", role: "group", "aria-label": "Market" }, ...data.markets.map((m) => h("button", { type: "button", class: "btn sm secondary", "aria-pressed": String(m.id === marketId), text: m.label, onclick: () => go({ tab: "markets", market: m.id }) })));
    const sortSelect = Aira.ui.select([["name", "Sort by name"], ["change", "Biggest rise first"], ["fall", "Biggest fall first"], ["volume", "Most traded first"]], route.query.sort || "name", { "aria-label": "Sort stocks" });
    sortSelect.addEventListener("change", () => go({ ...route.query, tab: "markets", sort: sortSelect.value }));
    const sorters = { name: (a, b) => a.name.localeCompare(b.name), change: (a, b) => b.change_pct - a.change_pct, fall: (a, b) => a.change_pct - b.change_pct, volume: (a, b) => b.volume - a.volume };
    const rows = [...data.stocks].sort(sorters[route.query.sort] || sorters.name);
    const accounts = mine ? mine.accounts.filter((a) => a.value_cents || a.kind === "practice") : [];
    put(body,
      h("div", { class: "btn-row st-top" }, switcher),
      accounts.length ? S.section("Your money today", h("div", { class: "st-cards" }, ...accounts.map((a) => h("a", { class: "card st-card", href: `#/stocks?tab=portfolio&kind=${a.kind}` }, h("span", { class: "muted small", text: a.kind === "practice" ? `Practice (${a.currency})` : `My holdings (${a.currency})` }), h("strong", { class: "st-big", text: money(a.kind === "practice" ? a.equity_cents : a.value_cents, a.currency) }), change(a.day_change_cents, `${signedMoney(a.day_change_cents, a.currency)} (${signedPct(a.day_change_pct)}) today`))))) : null,
      h("div", { class: "st-cards" }, ...data.indices.map((q) => h("a", { class: "card st-card", href: `#/stocks?symbol=${enc(q.symbol)}` }, h("span", { class: "muted small", text: q.name }), h("strong", { class: "st-big", text: num(q.price_cents / 100) }), quoteChange(q)))),
      h("p", { class: "muted small", text: `${data.label}: ${data.breadth.up} up, ${data.breadth.down} down, ${data.breadth.flat} unchanged. ${data.provider === "stub" ? "Practice data (offline market)." : "Prices from Yahoo Finance, may be delayed."} ${data.as_of ? `As of ${S.when(data.as_of)}.` : ""}${data.stale ? " Some prices are saved ones: the market data could not be reached." : ""}` }),
      h("div", { class: "st-movers-grid" },
        S.section("Top gainers today", moverList(data.gainers, "No stock is up today.", (r) => change(r.change_pct, signedPct(r.change_pct)))),
        S.section("Top losers today", moverList(data.losers, "No stock is down today.", (r) => change(r.change_pct, signedPct(r.change_pct)))),
        S.section("Most traded (volume)", moverList(data.active, "No trading yet.", (r) => h("span", { text: volume(r.volume) }))),
        S.section("Near their 52-week high", moverList(data.highs, "No stock is within 2% of its 52-week high.", (r) => h("span", { text: `${signedPct(r.from_high_pct)} from high` }))),
        S.section("Near their 52-week low", moverList(data.lows, "No stock is within 2% of its 52-week low.", (r) => h("span", { text: `${signedPct(r.from_low_pct)} from low` })))),
      S.section(`Every stock (${rows.length})`, h("div", { class: "btn-row" }, sortSelect), S.table({
        caption: "Every stock in this market",
        columns: [
          { label: "Stock", render: (r) => h("span", {}, stockLink(r.symbol, r.symbol.replace(/\.NS$/, "")), h("span", { class: "muted small st-sub", text: r.name })) },
          { label: "Price", render: (r) => priceOf(r.price_cents, r), num: true },
          { label: "Today", render: (r) => change(r.change_pct, signedPct(r.change_pct)), num: true },
          { label: "Volume", render: (r) => volume(r.volume), num: true },
          { label: "52-week range", render: (r) => `${priceOf(r.low52_cents, r)} – ${priceOf(r.high52_cents, r)}` },
        ],
        rows,
        onRow: (r) => go({ symbol: r.symbol }),
        empty: "No prices yet.",
      })),
      disclaimer(data.disclaimer));
  }

  // ---------------------------------------------------------------- watchlist
  async function watchlistTab(body) {
    const data = await api("/watchlist");
    const rerender = () => Aira.router.render();
    const newList = () => edit({ title: "New watchlist", fields: [{ name: "name", label: "Name", required: true, placeholder: "Banks, Long term, ..." }], submitLabel: "Create", onSave: async (form) => { await api("/watchlists", { method: "POST", body: form }); rerender(); } });
    put(body,
      h("div", { class: "btn-row st-top" }, searchBox((symbol) => addToWatchlist(symbol, rerender, data.lists), { placeholder: "Add a stock: Apple, AAPL, Infosys", label: "Add a stock to a watchlist" }), S.button("New watchlist", newList, { kind: "secondary" })),
      data.stale ? h("p", { class: "muted small", text: "Some prices are saved ones: the market data could not be reached." }) : null);
    if (!data.lists.length) {
      body.append(Aira.ui.empty("Your watchlist is empty", "Find a stock above and add it to follow its price every day. Open a stock to see its daily log.", S.button("Add Apple (AAPL)", () => addToWatchlist("AAPL", rerender))));
      return;
    }
    for (const list of data.lists) {
      const section = S.section(list.name,
        h("div", { class: "btn-row" },
          S.button("Rename", () => edit({ title: "Rename watchlist", fields: [{ name: "name", label: "Name", required: true }], values: { name: list.name }, onSave: async (form) => { await api(`/watchlists/${list.id}`, { method: "PATCH", body: form }); rerender(); } }), { kind: "ghost sm" }),
          data.lists.length > 1 ? S.button("Delete list", async () => { if (await S.confirmDelete(`the list "${list.name}"`)) { await api(`/watchlists/${list.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }) : null),
        S.table({
          caption: list.name,
          columns: [
            { label: "Stock", render: (r) => h("span", {}, stockLink(r.symbol, r.symbol.replace(/\.NS$/, "")), h("span", { class: "muted small st-sub", text: r.name })) },
            { label: "Price", render: (r) => (r.missing ? "Not available" : priceOf(r.price_cents, r)), num: true },
            { label: "Today", render: (r) => (r.missing ? "" : quoteChange(r)), num: true },
            { label: "Day range", render: (r) => (r.missing ? "" : `${priceOf(r.day_low_cents, r)} – ${priceOf(r.day_high_cents, r)}`) },
            { label: "", render: (r) => h("div", { class: "btn-row" }, S.button("Alert", () => newAlert(r.symbol, null, r), { kind: "ghost sm" }), S.button("Remove", async () => { await api(`/watchlist/items/${r.item_id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" })) },
          ],
          rows: list.items,
          onRow: (r) => go({ symbol: r.symbol }),
          empty: Aira.ui.empty("Nothing on this list yet", "Use the search box above to add stocks."),
        }));
      section.classList.add("st-watch");
      body.append(section);
    }
  }

  // ---------------------------------------------------------------- portfolio
  function holdingsTable(account, rerender) {
    const c = account.currency;
    return S.table({
      caption: `Holdings in ${account.name}`,
      columns: [
        { label: "Stock", render: (r) => h("span", {}, stockLink(r.symbol, r.symbol.replace(/\.NS$/, "")), h("span", { class: "muted small st-sub", text: r.name })) },
        { label: "Shares", render: (r) => qty(r.quantity), num: true },
        { label: "Average cost", render: (r) => money(r.average_cents, c), num: true },
        { label: "Price", render: (r) => money(r.price_cents, c), num: true },
        { label: "Value", render: (r) => money(r.value_cents, c), num: true },
        { label: "Profit or loss", render: (r) => change(r.unrealised_cents, `${signedMoney(r.unrealised_cents, c)} (${signedPct(r.unrealised_pct)})`), num: true },
        { label: "Today", render: (r) => change(r.day_change_cents, signedMoney(r.day_change_cents, c)), num: true },
        { label: "Share of portfolio", render: (r) => `${r.allocation_pct}%`, num: true },
        account.kind === "practice" ? { label: "", render: (r) => S.button("Sell", () => practiceTrade(r.symbol, "sell", rerender), { kind: "ghost sm" }) } : null,
      ].filter(Boolean),
      rows: account.holdings,
      empty: Aira.ui.empty("No holdings yet", account.kind === "practice" ? "Buy a stock with practice money to start." : "Record a trade you made with your broker."),
    });
  }

  function earners(account) {
    const c = account.currency;
    const line = (r) => h("li", { class: "life-line" }, h("span", { class: "life-line-text", text: `${r.name} (${r.symbol})${r.open ? "" : " · sold"}` }), change(r.total_cents, signedMoney(r.total_cents, c)));
    return S.grid(
      h("div", {}, h("h3", { class: "suite-h3", text: "Earned the most" }), account.best.length ? h("ul", { class: "life-lines" }, ...account.best.map(line)) : h("p", { class: "muted", text: "Nothing in profit yet." })),
      h("div", {}, h("h3", { class: "suite-h3", text: "Earned the least" }), account.worst.length ? h("ul", { class: "life-lines" }, ...account.worst.map(line)) : h("p", { class: "muted", text: "Nothing at a loss." })));
  }

  function transactionsTable(account, rerender) {
    const c = account.currency;
    const kindName = { buy: "Bought", sell: "Sold", dividend: "Dividend" };
    return S.table({
      caption: `Transactions in ${account.name}`,
      columns: [
        { label: "Date", render: (t) => S.day(t.trade_date) },
        { label: "What", render: (t) => `${kindName[t.kind]} ${t.symbol}` },
        { label: "Shares", render: (t) => (t.quantity ? qty(t.quantity) : ""), num: true },
        { label: "Price", render: (t) => (t.price_cents ? money(t.price_cents, c) : ""), num: true },
        { label: "Fees", render: (t) => (t.fees_cents ? money(t.fees_cents, c) : ""), num: true },
        { label: "Amount", render: (t) => money(t.kind === "dividend" ? t.amount_cents : Math.round(t.quantity * t.price_cents), c), num: true },
        { label: "Realised", render: (t) => (t.realised_cents === null || t.realised_cents === undefined ? "" : change(t.realised_cents, signedMoney(t.realised_cents, c))), num: true },
        account.kind === "real" ? { label: "", render: (t) => h("div", { class: "btn-row" }, S.button("Change", () => realTrade({}, rerender, { ...t, price_cents: t.price_cents || null, fees_cents: t.fees_cents || null, amount_cents: t.amount_cents || null }), { kind: "ghost sm" }), S.button("Delete", async () => { if (await S.confirmDelete("this trade")) { await api(`/real/transactions/${t.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })) } : null,
      ].filter(Boolean),
      rows: account.transactions.slice(0, 200),
      empty: "No transactions yet.",
    });
  }

  function accountSection(account, rerender) {
    const c = account.currency;
    const practice = account.kind === "practice";
    const kpis = practice ? [
      { label: "Worth now", value: money(account.equity_cents, c), hint: `Cash ${money(account.cash_cents, c)} + shares ${money(account.value_cents, c)}` },
      { label: "Since you started", value: signedPct(account.return_pct), tone: account.return_pct > 0 ? "good" : account.return_pct < 0 ? "bad" : "", hint: `${arrow(account.return_pct)} from ${money(account.start_cash_cents, c)}` },
      { label: "Today", value: signedMoney(account.day_change_cents, c), tone: account.day_change_cents > 0 ? "good" : account.day_change_cents < 0 ? "bad" : "", hint: `${arrow(account.day_change_cents)} ${signedPct(account.day_change_pct)}` },
      { label: "Not yet sold (unrealised)", value: signedMoney(account.unrealised_cents, c), hint: signedPct(account.unrealised_pct) },
      { label: "Already sold (realised)", value: signedMoney(account.realised_cents, c) },
    ] : [
      { label: "Value now", value: money(account.value_cents, c), hint: `Invested ${money(account.invested_cents, c)}` },
      { label: "Not yet sold (unrealised)", value: signedMoney(account.unrealised_cents, c), tone: account.unrealised_cents > 0 ? "good" : account.unrealised_cents < 0 ? "bad" : "", hint: `${arrow(account.unrealised_cents)} ${signedPct(account.unrealised_pct)}` },
      { label: "Already sold (realised)", value: signedMoney(account.realised_cents, c), hint: `Fees paid ${money(account.fees_cents, c)}` },
      { label: "Dividends", value: money(account.dividends_cents, c) },
      { label: "Today", value: signedMoney(account.day_change_cents, c), tone: account.day_change_cents > 0 ? "good" : account.day_change_cents < 0 ? "bad" : "", hint: `${arrow(account.day_change_cents)} ${signedPct(account.day_change_pct)}` },
      { label: "Total profit or loss", value: signedMoney(account.total_pl_cents, c) },
    ];
    const reset = () => edit({
      title: `Reset ${account.name}`,
      fields: [{ name: "cash_cents", label: "Start again with", type: "money", hint: "Every practice trade in this portfolio is removed." }],
      values: { cash_cents: account.start_cash_cents },
      submitLabel: "Reset",
      onSave: async (form) => { await api(`/practice/${account.id}/reset`, { method: "POST", body: form }); Aira.toast("Practice portfolio reset.", "success"); rerender(); },
    });
    return S.section(account.name,
      S.kpis(kpis),
      practice ? h("div", { class: "btn-row" }, S.button("Buy", () => practiceTrade("", "buy", rerender)), S.button("Sell", () => practiceTrade("", "sell", rerender), { kind: "secondary" }), S.button("Reset", reset, { kind: "ghost" })) : null,
      h("h3", { class: "suite-h3", text: "Holdings" }),
      holdingsTable(account, rerender),
      account.holdings.length || account.closed.length ? earners(account) : null,
      h("h3", { class: "suite-h3", text: "Transaction log" }),
      transactionsTable(account, rerender));
  }

  async function leagueSection() {
    const data = await api("/league");
    return S.section("Practice league",
      h("p", { class: "muted", text: data.hosted ? "Practice returns across your organization. Only names and returns are shown." : "Practice returns across your household. Only names and returns are shown." }),
      S.table({
        caption: "Practice league",
        columns: [
          { label: "Rank", render: (r) => `#${r.rank}`, num: true },
          { label: "Person", render: (r) => h("span", {}, h("span", { "aria-hidden": "true", text: `${r.emoji} ` }), h("span", { text: r.me ? `${r.name} (you)` : r.name })) },
          { label: "Return", render: (r) => change(r.return_pct, signedPct(r.return_pct)), num: true },
          { label: "Money", render: (r) => r.currency },
          { label: "Trades", render: (r) => String(r.trades), num: true },
        ],
        rows: data.rows,
        empty: Aira.ui.empty("Nobody is practising yet", "Start a practice portfolio: the best return leads the league."),
      }));
  }

  async function portfolioTab(body, route) {
    const kind = route.query.kind === "real" ? "real" : "practice";
    const rerender = () => Aira.router.render();
    const switcher = h("div", { class: "st-ranges", role: "group", "aria-label": "Portfolio" }, ...[["practice", "Practice (virtual money)"], ["real", "My real holdings"]].map(([id, text]) => h("button", { type: "button", class: "btn sm secondary", "aria-pressed": String(id === kind), text, onclick: () => go({ tab: "portfolio", kind: id }) })));
    put(body, switcher);
    let data;
    try { data = await api(`/portfolio?kind=${kind}`); } catch (error) {
      if (error.status === 403) { body.append(Aira.ui.empty("Real holdings are for adults", error.message)); return; }
      throw error;
    }
    if (kind === "practice") {
      put(body, h("p", { class: "muted", text: "Learn without risk: trade with virtual money at real prices. Trades fill at the latest price, without fees." }));
      const have = new Set(data.accounts.map((a) => a.currency));
      const start = (currency) => edit({
        title: "Start a practice portfolio",
        fields: [
          { name: "currency", label: "Money", type: "select", options: [["USD", "US dollars (for US stocks)"], ["INR", "Indian rupees (for NSE stocks)"]] },
          { name: "cash_cents", label: "Virtual cash to start with", type: "money", hint: "For example 100,000 dollars or 10,00,000 rupees." },
        ],
        values: { currency, cash_cents: data.defaults[currency] },
        submitLabel: "Start",
        onSave: async (form) => { await api("/practice/start", { method: "POST", body: form }); Aira.toast("Your practice portfolio is ready. Buy your first stock.", "success"); rerender(); },
      });
      const missing = ["USD", "INR"].filter((code) => !have.has(code));
      // "+ New > Buy with practice money": the trade form, or first the form that starts a practice portfolio.
      hooks.trade = (ask) => (data.accounts.length ? practiceTrade(ask.symbol || "", "buy", rerender) : start("USD"));
      if (!data.accounts.length) {
        body.append(Aira.ui.empty("Start practising", "Get virtual money and buy real stocks at today's prices. Nothing real is bought or sold.", h("div", { class: "btn-row" }, S.button("Start with 100,000 US dollars", () => start("USD")), S.button("Start with 10,00,000 rupees", () => start("INR"), { kind: "secondary" }))));
      } else {
        for (const account of data.accounts) body.append(accountSection(account, rerender));
        if (missing.length) body.append(h("div", { class: "btn-row" }, ...missing.map((code) => S.button(code === "INR" ? "Also practise with rupees (India)" : "Also practise with dollars (US)", () => start(code), { kind: "ghost" }))));
        body.append(h("div", { class: "btn-row" }, S.button("Export CSV", () => Aira.download("/api/stocks/export/transactions?kind=practice", "stock-practice-transactions.csv"), { kind: "ghost sm" })));
      }
      body.append(await leagueSection());
    } else {
      hooks.real = (ask) => realTrade(ask.symbol ? { symbol: ask.symbol } : {}, rerender);
      put(body,
        h("p", { class: "st-note", role: "note", text: "Monosage never buys or sells shares. Record here what you did with your own broker, and Monosage works out your profit and loss." }),
        h("div", { class: "btn-row" }, S.button("Record a real trade", () => realTrade({}, rerender)), data.accounts.length ? S.button("Export CSV", () => Aira.download("/api/stocks/export/transactions?kind=real", "stock-real-transactions.csv"), { kind: "ghost" }) : null));
      if (!data.accounts.length) body.append(Aira.ui.empty("No holdings recorded", "Add the shares you own: what you bought, when, at what price and the fees. Sells, partial sells and dividends work too.", S.button("Record your first trade", () => realTrade({}, rerender))));
      for (const account of data.accounts) body.append(accountSection(account, rerender));
    }
    body.append(disclaimer(data.disclaimer));
  }

  // ---------------------------------------------------------------- predictions
  const SIGNAL = { bullish: ["Bullish", "▲", "good"], bearish: ["Bearish", "▼", "bad"], neutral: ["Neutral", "■", ""] };

  async function predictionsTab(body, route) {
    const rerender = () => Aira.router.render();
    let symbol = route.query.symbol;
    const mine = await api("/predictions");
    if (!symbol) {
      const watch = await api("/watchlist", { silent: true }).catch(() => ({ lists: [] }));
      const first = watch.lists.flatMap((l) => l.items)[0];
      symbol = first ? first.symbol : "AAPL";
    }
    put(body, h("div", { class: "btn-row st-top" }, searchBox((s) => go({ tab: "predictions", symbol: s }), { placeholder: "Predict another stock", label: "Choose a stock to predict" })));
    const holder = h("div", { class: "suite-body" }, Aira.ui.skeleton(3));
    body.append(holder);
    const myList = h("div", {});
    body.append(myList);
    drawMine(myList, mine, rerender);
    let data;
    try { data = await api(`/predict?symbol=${enc(symbol)}`); } catch (error) { holder.replaceChildren(Aira.ui.error(error, rerender)); return; }
    const q = data.quote;
    hooks.prediction = () => predictForm(q, "", rerender);
    const fmt = (v) => (q.index ? num(v) : money(Math.round(v * 100), q.currency));
    const [label, mark, tone] = SIGNAL[data.signal.label];
    const f5 = data.forecasts["5"], f20 = data.forecasts["20"];
    const explain = h("p", { class: "st-explain", text: data.explanation.text });
    const ind = data.indicators;
    const indicatorRows = [
      ["Price", ind.price, ""], ["20-day average (SMA 20)", ind.sma20, ""], ["50-day average (SMA 50)", ind.sma50, ""], ["200-day average (SMA 200)", ind.sma200, "The long-term trend"],
      ["20-day exponential average (EMA 20)", ind.ema20, ""], ["RSI (14 days)", ind.rsi14, "Above 70: rose fast. Below 30: fell fast."], ["MACD", ind.macd, "Above its signal line: momentum rising"], ["MACD signal line", ind.macd_signal, ""],
      ["Bollinger upper band", ind.bollinger_upper, ""], ["Bollinger lower band", ind.bollinger_lower, ""], ["Volatility (yearly)", ind.volatility_pct, "How much the price swings"],
    ];
    const isMoney = (name) => !/RSI|MACD|Volatility/.test(name);
    const recordTable = (h_) => S.table({
      caption: `Track record over ${h_} trading days`,
      columns: [
        { label: "Model", render: (r) => r.label },
        { label: "Direction right", render: (r) => (r.direction_hit_pct === null ? "Not a direction" : `${r.direction_hit_pct}%`), num: true },
        { label: "Average miss", render: (r) => (r.mape_pct === null ? "n/a" : `${r.mape_pct}%`), num: true },
        { label: "Tests", render: (r) => String(r.tests), num: true },
      ],
      rows: ["trend", "smoothing", "ensemble", "naive"].map((m) => data.backtest[h_].models[m]),
    });
    holder.replaceChildren(
      h("section", { class: "card st-hero" },
        h("div", { class: "st-hero-main" },
          h("p", { class: "muted small", text: `${q.symbol} · ${priceOf(q.price_cents, q)}` }),
          h("h2", { class: "st-hero-name" }, h("a", { href: `#/stocks?symbol=${enc(q.symbol)}`, class: "st-link", text: data.name })),
          h("p", { class: `st-signal ${tone}` }, h("span", { "aria-hidden": "true", text: `${mark} ` }), h("strong", { text: `Signal: ${label}` }), h("span", { class: "muted", text: ` (score ${data.signal.score > 0 ? "+" : ""}${data.signal.score})` }))),
        h("ul", { class: "st-reasons" }, ...data.signal.reasons.map((r) => h("li", {}, h("span", { class: `st-chg ${toneOf(r.points)}`, "aria-hidden": "true", text: `${arrow(r.points)} ` }), h("span", { text: r.text }))))),
      disclaimer(data.disclaimer),
      S.section("Forecast",
        S.kpis([
          { label: "In 5 trading days", value: fmt(f5.models.ensemble.price), hint: `${arrow(f5.change_pct)} ${signedPct(f5.change_pct)} · 80% band ${fmt(f5.low)} – ${fmt(f5.high)}` },
          { label: "In 20 trading days", value: fmt(f20.models.ensemble.price), hint: `${arrow(f20.change_pct)} ${signedPct(f20.change_pct)} · 80% band ${fmt(f20.low)} – ${fmt(f20.high)}` },
        ]),
        lineChart({ dates: data.history.dates, lines: [{ values: data.history.close, cls: "st-price", name: "Close (last 60 days)" }], future: data.path, format: fmt, label: `${data.name}: last 60 days and the 20-day forecast` }),
        S.table({
          caption: "Each model's forecast",
          columns: [
            { label: "Model", render: (r) => r.label },
            { label: "5 days", render: (r) => h("span", {}, h("span", { text: `${fmt(r.p5.price)} ` }), change(r.p5.change_pct, signedPct(r.p5.change_pct))), num: true },
            { label: "20 days", render: (r) => h("span", {}, h("span", { text: `${fmt(r.p20.price)} ` }), change(r.p20.change_pct, signedPct(r.p20.change_pct))), num: true },
          ],
          rows: ["trend", "smoothing", "ensemble"].map((m) => ({ label: f5.models[m].label, p5: f5.models[m], p20: f20.models[m] })),
        }),
        h("div", { class: "btn-row" }, S.button("Explain in plain words", async () => { const again = await api(`/predict?symbol=${enc(q.symbol)}&explain=1`); explain.textContent = again.explanation.text; }, { kind: "secondary" })),
        explain),
      S.section("Track record: how often were the models right?",
        h("p", { class: "muted", text: `Monosage replayed the past: on ${data.backtest["5"].tests} past days it forecast with only what was known then, and compared with what really happened. "No change" is the baseline: a model is only useful if it misses by less.` }),
        S.grid(h("div", {}, h("h3", { class: "suite-h3", text: "5 trading days ahead" }), recordTable("5")), h("div", {}, h("h3", { class: "suite-h3", text: "20 trading days ahead" }), recordTable("20"))),
        h("p", { class: "muted small", text: `For comparison, the price simply went up over 20 days ${data.backtest["20"].went_up_pct}% of the time.` })),
      S.section("Indicators", S.table({
        caption: "Technical indicators",
        columns: [
          { label: "Indicator", render: (r) => r[0] },
          { label: "Value", render: (r) => (r[1] === null || r[1] === undefined ? "Not enough data" : isMoney(r[0]) ? fmt(r[1]) : r[0].startsWith("Volatility") ? `${r[1]}%` : String(r[1])), num: true },
          { label: "What it means", render: (r) => r[2] },
        ],
        rows: indicatorRows,
      })),
      S.section("Make your own prediction",
        h("p", { class: "muted", text: "Say where you think the price goes. Monosage checks it on the date you choose and keeps your score next to the models'." }),
        h("div", { class: "btn-row" },
          S.button(`I think ${q.symbol} goes up`, () => predictForm(q, "up", rerender)),
          S.button(`I think ${q.symbol} goes down`, () => predictForm(q, "down", rerender), { kind: "secondary" }))));
  }

  // Without a direction ("+ New > My prediction") the form asks for it.
  function predictForm(q, direction, rerender) {
    const inMonth = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
    return edit({
      title: direction ? `${q.symbol} goes ${direction}` : `My prediction for ${q.symbol}`,
      fields: [
        direction ? null : { name: "direction", label: `I think ${q.symbol} goes`, type: "select", options: [["up", "Up"], ["down", "Down"]] },
        { name: "target_date", label: "Check on", type: "date", required: true },
        { name: "target_cents", label: "Target price (optional)", type: "money", hint: `Now ${priceOf(q.price_cents, q)}.` },
        { name: "note", label: "Why? (optional)" },
      ].filter(Boolean),
      values: { target_date: inMonth, direction: "up" },
      submitLabel: "Save my prediction",
      onSave: async (form) => { await api("/predictions", { method: "POST", body: { ...form, symbol: q.symbol, direction: direction || form.direction } }); Aira.toast("Saved. Monosage checks it on that date.", "success"); rerender(); },
    });
  }

  function drawMine(node, mine, rerender) {
    const st = mine.stats;
    node.replaceChildren(S.section("My predictions",
      S.kpis([
        { label: "Waiting", value: st.open },
        { label: "Checked", value: st.scored },
        { label: "You were right", value: st.hit_pct === null ? "-" : `${st.hit_pct}%`, hint: st.scored ? `${st.hits} of ${st.scored}` : "Nothing checked yet" },
        { label: "The models were right", value: st.model_hit_pct === null ? "-" : `${st.model_hit_pct}%`, hint: "On the same predictions" },
      ]),
      S.table({
        caption: "My predictions",
        columns: [
          { label: "Stock", render: (p) => stockLink(p.symbol) },
          { label: "I said", render: (p) => change(p.direction === "up" ? 1 : -1, p.direction === "up" ? "Up" : "Down") },
          { label: "Price then", render: (p) => money(p.base_cents, p.currency), num: true },
          { label: "Check on", render: (p) => S.day(p.target_date) },
          { label: "Models said", render: (p) => (p.model_direction ? (p.model_direction === "up" ? "▲ Up" : "▼ Down") : "") },
          { label: "Result", render: (p) => (p.status === "open" ? pill(p.so_far === "even" ? "No change yet" : p.so_far ? `So far ${p.so_far}` : "Waiting", p.so_far === "right" ? "good" : p.so_far === "wrong" ? "warn" : "info") : pill(`${p.hit ? "✓ Right" : "✗ Wrong"} · ${money(p.actual_cents, p.currency)}`, p.hit ? "good" : "bad")) },
          { label: "", render: (p) => S.button("Delete", async () => { if (await S.confirmDelete("this prediction")) { await api(`/predictions/${p.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }) },
        ],
        rows: mine.predictions,
        empty: Aira.ui.empty("No predictions yet", "Pick a stock and say if it goes up or down. See how you do against the models."),
      }),
      mine.predictions.length ? h("div", { class: "btn-row" }, S.button("Export CSV", () => Aira.download("/api/stocks/export/predictions", "stock-predictions.csv"), { kind: "ghost sm" })) : null));
  }

  // ---------------------------------------------------------------- ideas (earning)
  const IDEA_STATES = { open: ["Open", "info"], target_hit: ["Target hit", "good"], stopped: ["Stopped out", "bad"], expired: ["Expired", ""], closed: ["Closed", ""] };
  const ideaState = (status) => { const [text, tone] = IDEA_STATES[status] || [status, ""]; return pill(text, tone); };

  async function ideasTab(body) {
    const rerender = () => Aira.router.render();
    let data;
    try { data = await api("/ideas"); } catch (error) {
      if (error.status === 403) { body.append(Aira.ui.empty("Earning is for adults", `${error.message} Ask an adult in your household to help.`)); return; }
      throw error;
    }
    const a = data.analyst;
    const e = data.earnings;
    const r = data.record;
    const editAnalyst = () => edit({
      title: "Your analyst page",
      fields: [
        { name: "display_name", label: "Public name", required: true },
        { name: "bio", label: "About you", type: "textarea", rows: 3, placeholder: "What you look for in a stock, how long you hold." },
        { name: "price_cents", label: "Monthly price for paid ideas", type: "money", hint: "0 means everything you publish is free." },
        { name: "currency", label: "Currency", type: "select", options: Object.keys(SYMBOLS).map((code) => [code, code]) },
        { name: "payment_link", label: "Your payment link", placeholder: "https://...", hint: "Your own Stripe, PayPal, Razorpay or UPI page. Monosage does not take payments." },
        { name: "payment_instructions", label: "Or payment instructions", type: "textarea", rows: 2, placeholder: "UPI id, bank details" },
        { name: "accepting", label: "Take new subscribers", type: "checkbox" },
      ],
      values: a || { display_name: "", currency: "USD", accepting: true },
      onSave: async (form) => { await api("/analyst", { method: "POST", body: form }); Aira.toast("Saved.", "success"); rerender(); },
    });
    const publish = () => edit({
      title: "Publish a stock idea",
      wide: true,
      fields: [
        { name: "symbol", label: "Stock symbol", required: true, placeholder: "AAPL or INFY.NS" },
        { name: "side", label: "Idea", type: "select", options: [["buy", "Buy (I expect it to rise)"], ["sell", "Sell (I expect it to fall)"]] },
        { name: "entry_cents", label: "Entry price (empty = today's price)", type: "money", hint: "Must be within 10% of today's price, to keep the record honest." },
        { name: "target_cents", label: "Target price", type: "money", required: true },
        { name: "stop_cents", label: "Stop-loss price", type: "money", required: true, hint: "Where you admit the idea was wrong." },
        { name: "horizon_days", label: "Time frame in days", type: "number", min: 1, max: 365, value: 30 },
        { name: "access", label: "Who can see it", type: "select", options: [["free", "Everyone (free)"], ["paid", "Paying subscribers only"]] },
        { name: "reasoning", label: "Why", type: "textarea", rows: 4 },
      ],
      values: { side: "buy", access: "free", horizon_days: 30 },
      submitLabel: "Publish",
      onSave: async (form) => { await api("/ideas", { method: "POST", body: form }); Aira.toast("Published. Monosage tracks it every day.", "success"); rerender(); },
    });
    const editReasoning = (idea) => edit({
      title: `Why: ${idea.side === "buy" ? "Buy" : "Sell"} ${idea.symbol}`,
      fields: [{ name: "reasoning", label: "Why", type: "textarea", rows: 5, hint: "Only the reasoning can change. The prices stay as published, so the record stays honest." }],
      values: { reasoning: idea.reasoning || "" },
      onSave: async (form) => { await api(`/ideas/${idea.id}`, { method: "PATCH", body: form }); Aira.toast("Saved.", "success"); rerender(); },
    });
    const markPaid = (sub) => edit({
      title: `Payment from ${sub.name}`,
      fields: [
        { name: "months", label: "Months paid", type: "number", min: 1, max: 24, value: 1 },
        { name: "amount_cents", label: "Amount received", type: "money", hint: a ? `Your price is ${money(a.price_cents, a.currency)} a month.` : "" },
        { name: "paid_on", label: "Paid on", type: "date" },
        { name: "note", label: "Note (optional)", placeholder: "Payment reference" },
      ],
      values: { months: 1, amount_cents: a ? a.price_cents : null, paid_on: new Date().toISOString().slice(0, 10) },
      submitLabel: "Confirm payment",
      onSave: async (form) => {
        const result = await api(`/subscribers/${sub.id}/paid`, { method: "POST", body: form });
        rerender();
        if (result.code) showCode(sub, result.code);
        else Aira.toast(result.message || "Payment recorded. The subscription is extended.", "success");
      },
    });
    const showCode = (sub, code) => Aira.modal({
      title: `Access code for ${sub.name}`,
      body: h("div", { class: "suite-body st-app" }, h("p", { text: "Monosage put an email with this code in your outbox (Business hub > Outbox). You can also send it yourself. It is shown only now." }), h("p", { class: "st-code", text: code }), S.button("Copy code", () => S.copy(code), { kind: "secondary" })),
      actions: [{ label: "Done", kind: "primary" }],
    });
    hooks.idea = publish;
    const SUB = { pending: ["Waiting for payment", "warn"], active: ["Active", "good"], expired: ["Ended", ""], cancelled: ["Cancelled", ""] };
    put(body,
      h("section", { class: "card st-earn" },
        h("h2", { class: "suite-h2", text: "Earn from your stock ideas" }),
        h("ol", { class: "st-steps" },
          h("li", { text: "Publish ideas with an entry, a target and a stop. Monosage tracks every one, so your record is real." }),
          h("li", { text: "Share your analyst page. Anyone can see your track record and your free ideas." }),
          h("li", { text: "Set a monthly price. Followers pay you through your own payment link; you confirm the payment and they get a code for your paid ideas." })),
        h("p", { class: "muted small", text: "Monosage does not move money: it records what you confirm. Ideas are opinions, not financial advice." })),
      S.kpis([
        { label: "Received in total", value: money(e.total_cents, e.currency), hint: `${e.payments} payments` },
        { label: "This month", value: money(e.this_month_cents, e.currency) },
        { label: "Active subscribers", value: e.active, hint: e.pending ? `${e.pending} waiting for payment` : "" , tone: e.pending ? "warn" : "" },
        { label: "Monthly income", value: money(e.monthly_cents, e.currency), hint: "Active subscribers × your price" },
      ]),
      S.grid(
        S.section("Your analyst page",
          a ? h("p", {}, h("strong", { text: a.display_name }), h("span", { class: "muted", text: ` · ${a.price_cents ? `${money(a.price_cents, a.currency)} a month` : "free only"}${a.accepting ? "" : " · not taking subscribers"}` })) : h("p", { class: "muted", text: "Set your public name, your price and how people pay you." }),
          h("div", { class: "btn-row" }, S.button(a ? "Edit" : "Set up", editAnalyst, { kind: a ? "ghost sm" : "" })),
          S.share({ title: "Public page", text: "Your track record and free ideas, for anyone with the link.", link: data.link, onCreate: async () => (await api("/analyst/link", { method: "POST", body: {} })).link, onRevoke: async () => { await api("/analyst/link", { method: "DELETE" }); } })),
        S.section("Your track record",
          S.kpis([
            { label: "Ideas", value: r.ideas, hint: `${r.open} open` },
            { label: "Win rate", value: r.win_rate_pct === null ? "-" : `${r.win_rate_pct}%`, hint: r.closed ? `${r.wins} of ${r.closed} closed ideas` : "No closed ideas yet" },
            { label: "Average return", value: r.average_return_pct === null || r.average_return_pct === undefined ? "-" : signedPct(r.average_return_pct), tone: r.average_return_pct > 0 ? "good" : r.average_return_pct < 0 ? "bad" : "" },
            { label: "Best / worst", value: r.best_pct === null ? "-" : `${signedPct(r.best_pct)} / ${signedPct(r.worst_pct)}` },
          ]))),
      S.section("Your ideas",
        h("div", { class: "btn-row" }, S.button("Publish an idea", publish), data.ideas.length ? S.button("Export CSV", () => Aira.download("/api/stocks/export/ideas", "stock-ideas.csv"), { kind: "ghost" }) : null),
        S.table({
          caption: "Your ideas",
          columns: [
            { label: "Idea", render: (i) => h("span", {}, h("strong", { text: `${i.side === "buy" ? "Buy" : "Sell"} ` }), stockLink(i.symbol), h("span", { class: "muted small st-sub", text: `${S.day(i.opened_on)} · ${i.access === "paid" ? "Paid" : "Free"}` })) },
            { label: "Entry", render: (i) => money(i.entry_cents, i.currency), num: true },
            { label: "Target / stop", render: (i) => `${money(i.target_cents, i.currency)} / ${money(i.stop_cents, i.currency)}`, num: true },
            { label: "Status", render: (i) => h("span", {}, ideaState(i.status), i.status === "open" ? h("span", { class: "muted small st-sub", text: `${i.days_left} days left` }) : null) },
            { label: "Return", render: (i) => change(i.return_pct, signedPct(i.return_pct)), num: true },
            { label: "", render: (i) => h("div", { class: "btn-row" },
              S.button("Edit why", () => editReasoning(i), { kind: "ghost sm" }),
              i.status === "open" ? S.button("Close now", async () => { if (await Aira.confirm({ title: "Close this idea", message: "It closes at today's price and counts in your track record.", confirmLabel: "Close idea" })) { await api(`/ideas/${i.id}`, { method: "PATCH", body: { close: true } }); rerender(); } }, { kind: "ghost sm" }) : null,
              i.deletable ? S.button("Delete", async () => { if (await Aira.confirm({ title: "Delete this idea", message: "In its first hour an idea can still be removed. After that it stays on your track record.", confirmLabel: "Delete idea", danger: true })) { await api(`/ideas/${i.id}`, { method: "DELETE" }); Aira.toast("Idea deleted.", "success"); rerender(); } }, { kind: "ghost sm" }) : null) },
          ],
          rows: data.ideas,
          empty: Aira.ui.empty("No ideas yet", "Publish your first idea. Its result counts from today.", S.button("Publish an idea", publish)),
        })),
      S.section("Subscribers",
        S.table({
          caption: "Subscribers",
          columns: [
            { label: "Person", render: (s) => h("span", {}, h("strong", { text: s.name }), h("span", { class: "muted small st-sub", text: s.email })) },
            { label: "Reference", render: (s) => s.reference },
            { label: "Status", render: (s) => { const [text, tone] = SUB[s.status] || [s.status, ""]; return h("span", {}, pill(text, tone), s.paid_until ? h("span", { class: "muted small st-sub", text: `Paid until ${S.day(s.paid_until)}` }) : null); } },
            { label: "Payments", render: (s) => h("ul", { class: "st-payments" }, ...(s.payments || []).map((p) => h("li", {}, h("span", { class: p.voided_at ? "st-void" : "", text: `${money(p.amount_cents, p.currency)} · ${S.day(p.paid_on)}${p.voided_at ? " (undone)" : ""}` }), p.voided_at ? null : S.button("Undo", async () => { if (await Aira.confirm({ title: "Undo this payment", message: "It stops counting as income and the paid period gets shorter.", confirmLabel: "Undo payment", danger: true })) { await api(`/payments/${p.id}/void`, { method: "POST", body: {} }); rerender(); } }, { kind: "ghost sm" })))) },
            { label: "", render: (s) => h("div", { class: "btn-row" },
              s.status !== "cancelled" ? S.button("Payment received", () => markPaid(s), { kind: "sm" }) : null,
              s.status === "active" ? S.button("New code", async () => { const result = await api(`/subscribers/${s.id}/code`, { method: "POST", body: {} }); rerender(); showCode(s, result.code); }, { kind: "ghost sm" }) : null,
              s.status !== "cancelled" ? S.button("Cancel", async () => { if (await Aira.confirm({ title: `Cancel ${s.name}`, message: "Their access code stops working.", confirmLabel: "Cancel subscription", danger: true })) { await api(`/subscribers/${s.id}/cancel`, { method: "POST", body: {} }); rerender(); } }, { kind: "ghost sm" }) : null) },
          ],
          rows: data.subscribers,
          empty: Aira.ui.empty("No subscribers yet", "Share your analyst page. People who want your paid ideas ask to subscribe there and pay you."),
        })),
      disclaimer(data.disclaimer));
  }

  // ---------------------------------------------------------------- alerts
  async function alertsTab(body) {
    const rerender = () => Aira.router.render();
    const data = await api("/alerts");
    hooks.alert = (ask) => newAlert(ask.symbol || "", rerender);
    put(body,
      h("div", { class: "btn-row" }, S.button("New alert", () => newAlert("", rerender)), S.button("Check now", async () => { const result = await api("/alerts/check", { method: "POST", body: {} }); Aira.toast(result.fired.length ? `${result.fired.length} alert${result.fired.length === 1 ? "" : "s"} fired.` : "No alert fired.", result.fired.length ? "success" : ""); rerender(); }, { kind: "secondary" })),
      h("p", { class: "muted", text: "Monosage checks your alerts every few minutes and tells you on Today and with a message on screen." }),
      S.table({
        caption: "Price alerts",
        columns: [
          { label: "Alert", render: (a) => h("span", {}, h("span", { text: a.text }), a.note ? h("span", { class: "muted small st-sub", text: a.note }) : null) },
          { label: "Status", render: (a) => (a.triggered ? h("span", {}, pill(a.active ? "Fired today, still on" : "Fired", "warn"), h("span", { class: "muted small st-sub", text: `${S.when(a.triggered_at)} at ${money(a.triggered_price_cents, a.currency)}` })) : pill(a.active ? "Waiting" : "Off", a.active ? "info" : "")) },
          { label: "", render: (a) => h("div", { class: "btn-row" },
            S.button(a.active ? "Turn off" : "Turn on again", async () => { await api(`/alerts/${a.id}`, { method: "PATCH", body: { active: !a.active } }); rerender(); }, { kind: "ghost sm" }),
            S.button("Delete", async () => { await api(`/alerts/${a.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" })) },
        ],
        rows: data.alerts,
        empty: Aira.ui.empty("No alerts yet", "Get told when a price rises above or falls below a number, or moves a lot in one day.", S.button("Create your first alert", () => newAlert("", rerender))),
      }));
  }

  // ---------------------------------------------------------------- search results (Enter in the search across the top)
  const MARKET_NAME = { us: "United States", in: "India (NSE)" };
  async function resultsPage(body, route) {
    const text = String(route.query.q || "").trim();
    const [found, us, india, watch] = await Promise.all([api(`/search?q=${enc(text)}`), cached("/markets?market=us"), cached("/markets?market=in"), cached("/watchlist")]);
    // Prices come from both markets' lists; a stock outside them shows its price on its own page.
    const quotes = new Map();
    for (const data of [us, india]) for (const q of data ? [...data.indices, ...data.stocks] : []) quotes.set(q.symbol, q);
    const watching = new Set(((watch && watch.lists) || []).flatMap((list) => list.items.map((item) => item.symbol)));
    const rows = found.results.map((r) => ({ ...r, quote: quotes.get(r.symbol) || null }));
    put(body,
      rows.length ? h("p", { class: "muted", text: `${rows.length} ${rows.length === 1 ? "match" : "matches"} for “${found.query}”.` }) : null,
      S.table({
        caption: `Stocks matching ${found.query}`,
        columns: [
          { label: "Stock", render: (r) => h("span", {}, stockLink(r.symbol, r.symbol.replace(/\.NS$/, "")), h("span", { class: "muted small st-sub", text: r.name })) },
          { label: "Market", render: (r) => `${MARKET_NAME[r.market] || r.market || ""}${r.index ? " · Index" : ""}` },
          { label: "Price", render: (r) => (r.quote ? priceOf(r.quote.price_cents, r.quote) : "Open to see"), num: true },
          { label: "Today", render: (r) => (r.quote ? change(r.quote.change_pct, signedPct(r.quote.change_pct)) : ""), num: true },
          { label: "", render: (r) => (watching.has(r.symbol) ? pill("On a watchlist", "good") : "") },
        ],
        rows,
        onRow: (r) => go({ symbol: r.symbol }),
        empty: Aira.ui.empty(`No stock matches “${found.query}”`, "Try a company name or a symbol. Indian shares end in .NS, for example TCS.NS.", h("div", { class: "btn-row" }, S.button("See every stock", () => go({ tab: "markets" })), S.button("India (NSE) market", () => go({ tab: "markets", market: "in" }), { kind: "secondary" }))),
      }),
      disclaimer());
  }

  // ---------------------------------------------------------------- in the Aira frame: "+ New" and the search
  // The tab each "+ New" lands on, and the portfolio it opens.
  const NEW_TAB = { trade: ["portfolio", "practice"], real: ["portfolio", "real"], alert: ["alerts"], prediction: ["predictions"], idea: ["ideas"] };
  // On a stock's page (or a prediction for one), "+ New" is about that stock.
  const openSymbol = () => {
    const current = Aira.router.current;
    return current && current.path[0] === "stocks" && current.query ? current.query.symbol || "" : "";
  };
  const ask = (kind) => () => go({ new: kind, symbol: openSymbol() || undefined });
  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  // "+ New": what people make most here, each opening the same form as the button on its tab.
  Aira.appNew.stocks = () => [
    { label: "Buy with practice money", icon: "stTrade", run: ask("trade") },
    { label: "Price alert", icon: "stBell", run: ask("alert") },
    { label: "My prediction", icon: "analytics", run: ask("prediction") },
    grownUp() ? { label: "Record a real trade", icon: "stWallet", run: ask("real") } : null,
    grownUp() ? { label: "Publish a stock idea", icon: "stIdea", run: ask("idea") } : null,
  ].filter(Boolean);
  Aira.appSearch.stocks = {
    placeholder: "Search stocks: Apple, AAPL, TCS.NS",
    // Stocks and indices by symbol or name, up to six, each saying when it is on one of the person's watchlists.
    async suggest(query) {
      const text = query.trim().slice(0, 40);
      if (!text) return [];
      const [found, watch] = await Promise.all([cached(`/search?q=${enc(text)}`), cached("/watchlist")]);
      const watching = new Set(((watch && watch.lists) || []).flatMap((list) => list.items.map((item) => item.symbol)));
      return ((found && found.results) || []).slice(0, 6).map((r) => ({
        label: r.name,
        sub: [r.symbol, r.index ? "Index" : MARKET_NAME[r.market], watching.has(r.symbol) ? "On a watchlist" : ""].filter(Boolean).join(" · "),
        icon: "stocks",
        href: `#/stocks?symbol=${enc(r.symbol)}`,
      }));
    },
    // Enter: every stock that matches, with today's price.
    run(query) { go({ q: query.trim().slice(0, 40) || undefined }); },
  };

  // ---------------------------------------------------------------- the page
  // The tabs, which the frame also shows as folders in the left menu; the page is named after the open one.
  const TABS = [
    { id: "markets", label: "Markets", icon: "stocks", about: "Indices, the day's biggest moves and every stock. Prices may be delayed." },
    { id: "watchlist", label: "Watchlist", icon: "stEye", about: "The stocks you follow, with today's prices." },
    { id: "portfolio", label: "Portfolio", icon: "stWallet", about: "Practise with virtual money, or keep the record of the shares you really own." },
    { id: "predictions", label: "Predictions", icon: "analytics", about: "What the models expect, how often they were right, and your own calls." },
    { id: "ideas", label: "Ideas (earn)", icon: "stIdea", about: "Publish stock ideas, build a real track record and earn from subscribers." },
    { id: "alerts", label: "Alerts", icon: "stBell", about: "Get told when a price crosses a number or moves a lot in a day." },
  ];

  Aira.views.stocks = async (main, route) => {
    // "+ New" asks with ?new=<kind> (&symbol=): remember the ask, take it out of the address (Back or a refresh must
    // not ask again) and draw its tab; the drawn tab then answers it with its own form.
    if (route.query.new) {
      const kind = String(route.query.new);
      pending = { new: kind, symbol: route.query.symbol || "" };
      const [tabId, kindOf] = NEW_TAB[kind] || ["markets"];
      Aira.router.go(["stocks"], { tab: tabId, kind: kindOf, symbol: tabId === "predictions" ? route.query.symbol : undefined }, { replace: true });
      return;
    }
    const wanted = pending;
    pending = null;
    answers.clear();
    for (const key of Object.keys(hooks)) delete hooks[key];
    const onStock = route.query.symbol && !route.query.tab;
    const searching = !onStock && !route.query.tab && String(route.query.q || "").trim();
    const current = onStock || searching ? TABS[0] : TABS.find((t) => t.id === route.query.tab) || TABS[0];
    const title = onStock ? String(route.query.symbol).toUpperCase() : searching ? `Results for “${searching.slice(0, 40)}”` : current.label;
    const body = S.page(main, { title, subtitle: onStock ? "" : searching ? "Stocks and indices whose symbol or name matches." : current.about });
    body.classList.add("st-app");
    // A stock's page renames itself once the stock is known.
    const retitle = (name, line) => {
      const head = main.querySelector(".page-head > div");
      if (!head) return;
      head.querySelector("h1").textContent = name;
      let sub = head.querySelector("p");
      if (!sub) { sub = h("p", {}); head.append(sub); }
      sub.textContent = line;
    };
    const tab = S.tabs(body, route, "stocks", TABS, current.id);
    const panel = h("div", { class: "suite-body" }, Aira.ui.skeleton(5));
    body.append(panel);
    const holder = h("div", { class: "suite-body" });
    try {
      if (onStock) await stockPage(holder, route, retitle);
      else if (searching) await resultsPage(holder, route);
      else if (tab === "watchlist") await watchlistTab(holder);
      else if (tab === "portfolio") await portfolioTab(holder, route);
      else if (tab === "predictions") await predictionsTab(holder, route);
      else if (tab === "ideas") await ideasTab(holder);
      else if (tab === "alerts") await alertsTab(holder);
      else await marketsTab(holder, route);
      panel.replaceChildren(holder);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    if (wanted && hooks[wanted.new]) await hooks[wanted.new](wanted);
  };
})();

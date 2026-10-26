/* Aira — public pages a business shares with its customers (/p/<token>; aira/suite/public.py).
 *
 * This page does not load the signed-in app (and runs no inline script: the page's policy allows none). It reads the token from the address, asks GET /api/public/<token>
 * for the page ({app, kind, title, business, actions, page}), brands itself with the business's name, logo and
 * colour, and hands the page to the renderer the app registered:
 *
 *   AiraPublic.register("bookings", "booking_page", (root, data, ctx) => { ... });
 *
 * ctx: { h, token, business, money(cents), date(iso), time(iso), datetime(iso), action(name, payload) -> data,
 *        reload(), notice(message, tone), field(label, input, hint), button(label, onClick, kind), copy(text) }
 * Every action posts JSON to /api/public/<token>/<name>; ctx.query(name, params) GETs a read-only query (polling);
 * errors come back as thrown Error with .status.
 * DOM is built with textContent only: nothing the server sends is parsed as HTML.
 */
"use strict";
(() => {
  const RENDERERS = {};
  const $ = (id) => document.getElementById(id);

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "text") node.textContent = String(value);
      else if (key === "class") node.className = value;
      else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (value === true) node.setAttribute(key, "");
      else if (key in node && key !== "list" && key !== "form") { try { node[key] = value; } catch { node.setAttribute(key, value); } }
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const token = (location.pathname.match(/^\/p\/([A-Za-z0-9_-]{40,64})$/) || [])[1] || "";
  const SYMBOLS = { USD: "$", EUR: "€", GBP: "£", INR: "₹", CAD: "CA$", AUD: "A$", SGD: "S$", AED: "AED ", JPY: "¥" };
  let business = { currency: "USD" };

  const money = (cents, currency) => {
    const code = currency || business.currency || "USD";
    const value = Number(cents || 0) / 100;
    const symbol = SYMBOLS[code] || `${code} `;
    if (code === "JPY") return `${symbol}${Math.round(value).toLocaleString()}`;
    return `${symbol}${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };
  const date = (iso) => (iso ? new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" }) : "");
  const time = (iso) => (iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "");
  const datetime = (iso) => (iso ? `${date(iso)}, ${time(iso)}` : "");

  async function request(method, path, body, extraHeaders) {
    const response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "aira", ...(extraHeaders || {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
    let data = {};
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) {
      const error = new Error(data.error || `The request failed (${response.status}).`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function notice(message, tone = "") {
    const node = h("div", { class: `pub-notice ${tone}`, role: tone === "bad" ? "alert" : "status", text: message });
    const main = $("pubMain");
    main.prepend(node);
    node.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return node;
  }

  const field = (label, input, hint) => {
    const id = input.id || (input.id = `p${Math.random().toString(36).slice(2, 8)}`);
    return h("div", { class: "pub-field" }, h("label", { for: id, text: label }), input, hint ? h("span", { class: "pub-muted pub-hint", text: hint }) : null);
  };
  const button = (label, onClick, kind = "") => {
    const node = h("button", { type: "button", class: `pub-btn ${kind}`.trim(), text: label });
    node.addEventListener("click", async (event) => {
      if (node.disabled) return;
      node.disabled = true;
      node.setAttribute("aria-busy", "true");
      try { await onClick(event); } catch (error) { notice(error.message, "bad"); } finally { node.disabled = false; node.removeAttribute("aria-busy"); }
    });
    return node;
  };
  const copy = async (text) => { try { await navigator.clipboard.writeText(text); notice("Copied.", "good"); } catch { /* the text stays visible */ } };

  function brand(data) {
    business = data.business || business;
    document.title = data.title && data.title !== business.name ? `${data.title} · ${business.name || "Aira"}` : business.name || data.title || "Aira";
    if (business.accent && /^#[0-9a-f]{6}$/i.test(business.accent)) document.documentElement.style.setProperty("--pub-accent", business.accent);
    $("pubHeader").hidden = false;
    $("pubName").textContent = business.name || "";
    $("pubTagline").textContent = business.tagline || "";
    if (business.logo_url && /^https?:\/\//i.test(business.logo_url)) { $("pubLogo").src = business.logo_url; $("pubLogo").hidden = false; }
    const contact = [business.email ? h("a", { href: `mailto:${business.email}`, text: business.email }) : null, business.phone ? h("a", { href: `tel:${business.phone.replace(/[^0-9+]/g, "")}`, text: business.phone }) : null, business.website && /^https?:\/\//i.test(business.website) ? h("a", { href: business.website, target: "_blank", rel: "noopener noreferrer", text: business.website.replace(/^https?:\/\//, "") }) : null, business.address ? h("span", { text: business.address }) : null].filter(Boolean);
    $("pubContact").replaceChildren(...contact);
    $("pubFooter").hidden = false;
  }

  // The business's own ways to be paid, for renderers that show an amount due.
  function paymentBox(amountCents, reference) {
    const parts = [];
    if (business.payment_link && /^https?:\/\//i.test(business.payment_link)) parts.push(h("a", { class: "pub-btn", href: business.payment_link, target: "_blank", rel: "noopener noreferrer", text: amountCents ? `Pay ${money(amountCents)}` : "Pay now" }));
    if (business.payment_instructions) parts.push(h("p", { class: "pub-pre", text: business.payment_instructions }));
    if (reference) parts.push(h("p", { class: "pub-muted", text: `Use reference ${reference} with your payment.` }));
    if (!parts.length) return null;
    return h("section", { class: "pub-card pub-pay" }, h("h2", { text: "How to pay" }), ...parts);
  }

  // A read-only question the page asks often (GET /api/public/<token>/<name>); a "secret" travels in a header.
  function query(name, params) {
    const { secret, ...rest } = params || {};
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(rest)) if (value !== undefined && value !== null) search.set(key, String(value));
    const suffix = search.toString() ? `?${search}` : "";
    return request("GET", `/api/public/${token}/${name}${suffix}`, undefined, secret ? { "Aira-Secret": String(secret) } : undefined);
  }

  async function load() {
    const main = $("pubMain");
    if (!token) { main.replaceChildren(h("h1", { text: "Page not found" }), h("p", { class: "pub-muted", text: "Check the link you were sent." })); return; }
    let data;
    try { data = await request("GET", `/api/public/${token}`); } catch (error) {
      document.title = "Not available";
      main.replaceChildren(h("h1", { text: error.status === 404 ? "This link is no longer available" : "Something went wrong" }), h("p", { class: "pub-muted", text: error.status === 404 ? "Ask the business for a new link." : error.message }));
      return;
    }
    brand(data);
    const render = RENDERERS[`${data.app}/${data.kind}`];
    const ctx = { h, token, business, money, date, time, datetime, notice, field, button, copy, paymentBox, reload: load, action: (name, payload) => request("POST", `/api/public/${token}/${name}`, payload || {}), query };
    main.replaceChildren();
    if (!render) { main.append(h("h1", { text: data.title || "Welcome" }), h("p", { class: "pub-muted", text: "This page cannot be shown here yet." })); return; }
    try { await render(main, data, ctx); } catch (error) { main.append(h("p", { class: "pub-notice bad", text: error.message })); }
  }

  window.AiraPublic = {
    register(app, kind, render) { RENDERERS[`${app}/${kind}`] = render; },
    start: load,
    h,
    money,
  };
  // Every app's public-<app>.js registers before this fires (they load after this file, without defer).
  document.addEventListener("DOMContentLoaded", load);
})();

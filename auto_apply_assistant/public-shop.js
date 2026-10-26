/* Aira — Shop: the pages customers open (aira/suite/shop).
 *
 * storefront: products with categories and search, a cart kept in this browser, a discount code, pickup or
 *             delivery, a checkout that the shop prices on its own server (quote, then place_order), and the
 *             confirmation with how to pay.
 * order:      one order's status, items and totals, how to pay, and "I have paid" (the shop confirms it).
 * Built with ctx.h and text only; classes from public.css.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  if (!P) return;

  const storageKey = (token) => `aira.shop.cart.${token.slice(0, 16)}`;
  const loadCart = (token) => { try { const raw = localStorage.getItem(storageKey(token)); const value = raw ? JSON.parse(raw) : []; return Array.isArray(value) ? value : []; } catch { return []; } };
  const saveCart = (token, cart) => { try { localStorage.setItem(storageKey(token), JSON.stringify(cart)); } catch { /* storage unavailable: the cart lives for this visit */ } };

  P.register("shop", "storefront", (root, data, ctx) => {
    const { h, money } = ctx;
    const page = data.page;
    const products = page.products || [];
    const byId = Object.fromEntries(products.map((p) => [p.id, p]));
    let cart = loadCart(ctx.token).filter((line) => byId[line.product_id]);
    let category = "";
    let text = "";
    let quote = null;
    let discount = "";
    let method = page.pickup.enabled ? "pickup" : "delivery";

    const grid = h("div", { class: "pub-grid" });
    const cartBox = h("section", { class: "pub-card", "aria-label": "Your cart" });
    const head = h("div", { class: "pub-card" },
      h("h1", { text: data.business.name || data.title || "Shop" }),
      data.business.tagline ? h("p", { class: "pub-muted", text: data.business.tagline }) : null,
      page.notice ? h("p", { class: "pub-notice", text: page.notice }) : null,
      !page.open ? h("p", { class: "pub-notice bad", text: page.closed_message }) : null,
      page.delivery.enabled && page.delivery.free_over_cents ? h("p", { class: "pub-muted", text: `Free delivery on orders over ${money(page.delivery.free_over_cents)}.` }) : null);

    const search = h("input", { type: "search", placeholder: "Search products", "aria-label": "Search products" });
    search.addEventListener("input", () => { text = search.value.trim().toLowerCase(); renderGrid(); });
    const chips = h("div", { class: "pub-btn-row" });
    const renderChips = () => {
      chips.replaceChildren(...["", ...page.categories].map((name) => {
        const chip = h("button", { type: "button", class: "pub-slot", "aria-pressed": String(category === name), text: name || "All" });
        chip.addEventListener("click", () => { category = name; renderChips(); renderGrid(); });
        return chip;
      }));
    };

    function addToCart(product, option, quantity) {
      const existing = cart.find((line) => line.product_id === product.id && line.option === option);
      if (existing) existing.quantity = Math.min(99, existing.quantity + quantity);
      else cart.push({ product_id: product.id, option, quantity });
      saveCart(ctx.token, cart);
      renderCart();
      cartBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    function card(product) {
      const options = product.options || [];
      const select = options.length ? h("select", { "aria-label": `Option for ${product.name}` }, ...options.map((o) => h("option", { value: o.name, disabled: !o.available, text: `${o.name}${o.price_cents !== product.price_cents ? ` · ${money(o.price_cents)}` : ""}${!o.available ? " (sold out)" : o.left ? ` (only ${o.left} left)` : ""}` }))) : null;
      if (select) { const firstAvailable = options.find((o) => o.available); if (firstAvailable) select.value = firstAvailable.name; }
      const quantity = h("input", { type: "number", min: 1, max: 99, value: 1, "aria-label": `Quantity of ${product.name}`, style: { width: "72px" } });
      const details = h("p", { class: "pub-pre", text: product.description || "", hidden: true });
      const more = product.description ? h("button", { type: "button", class: "pub-btn ghost", text: "Details" }) : null;
      if (more) more.addEventListener("click", () => { details.hidden = !details.hidden; more.textContent = details.hidden ? "Details" : "Hide details"; });
      const add = h("button", { type: "button", class: "pub-btn", text: product.in_stock ? "Add to cart" : "Sold out", disabled: !product.in_stock || !page.open });
      add.addEventListener("click", () => addToCart(product, select ? select.value : "", Math.max(1, Math.min(99, Number(quantity.value) || 1))));
      return h("article", { class: "pub-item" },
        product.images && product.images[0] ? h("img", { src: product.images[0], alt: product.name, loading: "lazy" }) : null,
        h("h3", { text: product.name }),
        h("div", {}, h("span", { class: "pub-price", text: money(product.price_cents) }), product.compare_at_cents ? h("s", { class: "pub-muted", text: ` ${money(product.compare_at_cents)}` }) : null),
        product.left ? h("span", { class: "pub-badge bad", text: `Only ${product.left} left` }) : null,
        select ? ctx.field("Option", select) : null,
        h("div", { class: "pub-btn-row" }, quantity, add),
        more, details);
    }

    function renderGrid() {
      const visible = products.filter((p) => (!category || p.category === category) && (!text || `${p.name} ${p.description} ${p.category}`.toLowerCase().includes(text)));
      grid.replaceChildren(...(visible.length ? visible.map(card) : [h("p", { class: "pub-muted", text: products.length ? "Nothing matches. Try another search." : "No products yet. Check back soon." })]));
    }

    let timer = null;
    const requote = () => {
      clearTimeout(timer);
      if (!cart.length) { quote = null; renderTotals(); return; }
      timer = setTimeout(async () => {
        try { quote = (await ctx.action("quote", { items: cart, discount_code: discount, delivery_method: method })).quote; totalsError.textContent = ""; }
        catch (error) { quote = null; totalsError.textContent = error.message; }
        renderTotals();
      }, 200);
    };

    const totals = h("div");
    const totalsError = h("p", { class: "pub-notice bad", role: "alert" });
    function renderTotals() {
      totalsError.hidden = !totalsError.textContent;
      if (!quote) { totals.replaceChildren(); return; }
      const row = (label, value, strong) => h("tr", {}, h("td", { text: label }), h("td", { class: `num${strong ? " pub-total" : ""}`, text: value }));
      totals.replaceChildren(h("table", { class: "pub-table" }, h("tbody", {},
        row("Subtotal", money(quote.subtotal_cents)),
        quote.discount_cents ? row(`Discount (${quote.discount_code})`, `-${money(quote.discount_cents)}`) : null,
        quote.delivery_method === "delivery" ? row("Delivery", quote.delivery_cents ? money(quote.delivery_cents) : "Free") : null,
        quote.tax_cents ? row(quote.tax_label, money(quote.tax_cents)) : null,
        row("Total", money(quote.total_cents), true))));
    }

    const name = h("input", { autocomplete: "name", required: true });
    const email = h("input", { type: "email", autocomplete: "email" });
    const phone = h("input", { type: "tel", autocomplete: "tel" });
    const address = h("textarea", { autocomplete: "street-address", rows: 3 });
    const note = h("textarea", { rows: 2, placeholder: "Anything the shop should know" });
    const code = h("input", { placeholder: "Discount code", "aria-label": "Discount code", style: { maxWidth: "180px" } });
    const addressField = ctx.field("Delivery address", address);

    function renderCart() {
      if (!cart.length) {
        cartBox.replaceChildren(h("h2", { text: "Your cart" }), h("p", { class: "pub-muted", text: "Your cart is empty." }));
        requote();
        return;
      }
      const lines = cart.map((line, index) => {
        const product = byId[line.product_id];
        const option = (product.options || []).find((o) => o.name === line.option);
        const unit = option ? option.price_cents : product.price_cents;
        const qty = h("input", { type: "number", min: 1, max: 99, value: line.quantity, "aria-label": `Quantity of ${product.name}`, style: { width: "64px" } });
        qty.addEventListener("change", () => { line.quantity = Math.max(1, Math.min(99, Number(qty.value) || 1)); saveCart(ctx.token, cart); renderCart(); });
        const remove = h("button", { type: "button", class: "pub-btn ghost", text: "Remove" });
        remove.addEventListener("click", () => { cart.splice(index, 1); saveCart(ctx.token, cart); renderCart(); });
        return h("tr", {}, h("td", { text: `${product.name}${line.option ? ` (${line.option})` : ""}` }), h("td", {}, qty), h("td", { class: "num", text: money(unit * line.quantity) }), h("td", {}, remove));
      });
      const methods = h("div", { class: "pub-btn-row", role: "radiogroup", "aria-label": "Pickup or delivery" });
      const renderMethods = () => {
        methods.replaceChildren(...[["pickup", "Pickup", page.pickup.enabled], ["delivery", `Delivery${page.delivery.fee_cents ? ` (${money(page.delivery.fee_cents)})` : ""}`, page.delivery.enabled]].filter(([, , on]) => on).map(([value, label]) => {
          const button = h("button", { type: "button", class: "pub-slot", role: "radio", "aria-checked": String(method === value), "aria-pressed": String(method === value), text: label });
          button.addEventListener("click", () => { method = value; addressField.hidden = method !== "delivery"; renderMethods(); requote(); });
          return button;
        }));
      };
      renderMethods();
      addressField.hidden = method !== "delivery";
      const apply = h("button", { type: "button", class: "pub-btn secondary", text: "Apply" });
      apply.addEventListener("click", () => { discount = code.value.trim(); requote(); });
      const place = ctx.button(page.open ? "Place order" : "The shop is closed", async () => {
        if (!name.value.trim()) { name.focus(); throw new Error("Enter your name."); }
        if (!email.value.trim() && !phone.value.trim()) { email.focus(); throw new Error("Enter an email address or a phone number."); }
        const result = await ctx.action("place_order", { items: cart, discount_code: discount, delivery_method: method, name: name.value, email: email.value, phone: phone.value, address: method === "delivery" ? address.value : "", note: note.value });
        cart = [];
        saveCart(ctx.token, cart);
        confirmation(result);
      });
      place.disabled = !page.open;
      cartBox.replaceChildren(
        h("h2", { text: `Your cart (${cart.reduce((sum, line) => sum + line.quantity, 0)})` }),
        h("table", { class: "pub-table" }, h("tbody", {}, ...lines)),
        page.has_discounts ? h("div", { class: "pub-btn-row" }, code, apply) : null,
        page.pickup.enabled && page.pickup.instructions && method === "pickup" ? h("p", { class: "pub-muted", text: page.pickup.instructions }) : null,
        methods,
        totals, totalsError,
        h("div", { class: "pub-form" }, ctx.field("Your name", name), ctx.field("Email", email, "For your order confirmation"), ctx.field("Phone", phone), addressField, ctx.field("Note", note)),
        h("div", { class: "pub-btn-row" }, place));
      requote();
    }

    function confirmation(result) {
      const order = result.order;
      root.replaceChildren(
        h("section", { class: "pub-card" },
          h("p", { class: "pub-badge good", text: "Order placed" }),
          h("h1", { text: `Thank you, ${order.customer_name.split(" ")[0]}!` }),
          h("p", { text: `Your order number is #${order.number}. Total ${money(order.total_cents, order.currency)}.` }),
          h("p", { class: "pub-muted", text: order.delivery_method === "delivery" ? "We will let you know when it ships." : "We will let you know when it is ready to pick up." }),
          h("div", { class: "pub-btn-row" }, h("a", { class: "pub-btn secondary", href: result.link, text: "See your order" }), ctx.button("Copy order link", () => ctx.copy(`${location.origin}${result.link}`), "ghost"))),
        ctx.paymentBox(order.total_cents, `#${order.number}`));
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    renderChips();
    renderGrid();
    renderCart();
    root.append(...[head, products.length > 6 || page.categories.length ? h("div", { class: "pub-card" }, ctx.field("Search", search), page.categories.length ? chips : null) : null, grid, cartBox].filter(Boolean));
  });

  P.register("shop", "order", (root, data, ctx) => {
    const { h, money } = ctx;
    const order = data.page;
    const cancelled = order.fulfilment_status === "cancelled";
    const done = new Set((order.history || []).map((entry) => entry.status));
    const steps = order.steps.map((step) => {
      const at = (order.history || []).filter((entry) => entry.status === step).pop();
      const label = step === "delivered" && order.delivery_method === "pickup" ? "picked up" : step;
      return h("li", { class: done.has(step) || order.steps.indexOf(step) <= order.steps.indexOf(order.fulfilment_status) ? "pub-done" : "" }, h("strong", { text: `${done.has(step) || order.steps.indexOf(step) <= order.steps.indexOf(order.fulfilment_status) ? "✓ " : "○ "}${label[0].toUpperCase()}${label.slice(1)}` }), at ? h("span", { class: "pub-muted", text: ` ${ctx.datetime(at.at)}` }) : null);
    });
    const payment = { unpaid: ["Waiting for payment", ""], paid: ["Paid", "good"], refunded: ["Refunded", "bad"] }[order.payment_status] || [order.payment_status, ""];
    const row = (label, value, strong) => h("tr", {}, h("td", { text: label }), h("td", { class: `num${strong ? " pub-total" : ""}`, text: value }));
    const reference = h("input", { placeholder: "Payment reference (optional)", "aria-label": "Payment reference" });
    root.append(
      h("section", { class: "pub-card" },
        h("h1", { text: `Order #${order.number}` }),
        h("div", { class: "pub-btn-row" }, h("span", { class: `pub-badge ${payment[1]}`, text: payment[0] }), cancelled ? h("span", { class: "pub-badge bad", text: "Cancelled" }) : null),
        h("p", { class: "pub-muted", text: `Placed ${ctx.datetime(order.created_at)} · ${order.delivery_method === "delivery" ? `Delivery to ${order.address}` : "Pickup"}` }),
        !cancelled ? h("ol", { class: "pub-form", style: { listStyle: "none", padding: "0" } }, ...steps) : null,
        order.tracking_url && /^https?:\/\//i.test(order.tracking_url) ? h("a", { class: "pub-btn secondary", href: order.tracking_url, target: "_blank", rel: "noopener noreferrer", text: "Track your parcel" }) : null),
      h("section", { class: "pub-card" },
        h("h2", { text: "Items" }),
        h("table", { class: "pub-table" }, h("tbody", {},
          ...order.items.map((line) => row(`${line.quantity} × ${line.name}${line.option ? ` (${line.option})` : ""}`, money(line.line_cents, order.currency))),
          row("Subtotal", money(order.subtotal_cents, order.currency)),
          order.discount_cents ? row(`Discount (${order.discount_code})`, `-${money(order.discount_cents, order.currency)}`) : null,
          order.delivery_method === "delivery" ? row("Delivery", order.delivery_cents ? money(order.delivery_cents, order.currency) : "Free") : null,
          order.tax_cents ? row("Tax", money(order.tax_cents, order.currency)) : null,
          row("Total", money(order.total_cents, order.currency), true)))),
    );
    if (order.payment_status === "unpaid" && !cancelled) {
      const box = ctx.paymentBox(order.total_cents, `#${order.number}`);
      if (box) root.append(box);
      if (order.customer_says_paid) root.append(h("p", { class: "pub-notice", text: "Thanks! The shop will confirm your payment shortly." }));
      else root.append(h("section", { class: "pub-card" }, h("h2", { text: "Already paid?" }), h("p", { class: "pub-muted", text: "Let the shop know so they can check and confirm it." }), ctx.field("Reference", reference), h("div", { class: "pub-btn-row" }, ctx.button("I have paid", async () => { await ctx.action("notify_paid", { reference: reference.value }); await ctx.reload(); }))));
    }
  });
})();

/* Aira — Shop: the pages customers open (aira/suite/shop).
 *
 * storefront: products with categories and search, a cart kept in this browser, a discount code (or a friend's
 *             share code from ?ref= in the link), pickup or delivery, a checkout that the shop prices on its own
 *             server (quote, then place_order), the confirmation with how to pay, and "Email me when it's back"
 *             on sold-out products (notify_me).
 *             A shop that sells to businesses (B2B) adds "Buying for a business?": a business applies for a trade
 *             account (trade_apply) or signs in with its email and trade code (trade_sign_in, kept for this browser
 *             session). Signed in, it sees trade prices, minimum quantities and price breaks, and checks out with a
 *             PO number on its payment terms. A shop for businesses only shows no prices until then.
 * order:      one order's status, items and totals, how to pay, "I have paid" (the shop confirms it) and, once
 *             paid, the customer's own share code ("Share and earn"). A business order is also its invoice: who
 *             sells and who buys, tax numbers, the PO number, terms and the due date, with Print.
 * Built with ctx.h and text only; classes from public.css.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  if (!P) return;

  const storageKey = (token, business) => `aira.shop.cart.${token.slice(0, 16)}${business ? ".b2b" : ""}`;
  const loadCart = (token, business) => { try { const raw = localStorage.getItem(storageKey(token, business)); const value = raw ? JSON.parse(raw) : []; return Array.isArray(value) ? value : []; } catch { return []; } };
  const saveCart = (token, business, cart) => { try { localStorage.setItem(storageKey(token, business), JSON.stringify(cart)); } catch { /* storage unavailable: the cart lives for this visit */ } };
  // A business's sign-in lasts for this browser session only: the trade code lets anyone order on its account.
  const tradeKey = (token) => `aira.shop.trade.${token.slice(0, 16)}`;
  const loadTrade = (token) => { try { const value = JSON.parse(sessionStorage.getItem(tradeKey(token)) || "null"); return value && value.email && value.code ? value : null; } catch { return null; } };
  const saveTrade = (token, value) => { try { if (value) sessionStorage.setItem(tradeKey(token), JSON.stringify(value)); else sessionStorage.removeItem(tradeKey(token)); } catch { /* the sign-in lasts for this page */ } };
  // Small inputs that sit in a row (quantity, a code) look like the page's other fields.
  const INPUT = { minHeight: "42px", padding: "8px 10px", border: "1px solid var(--pub-line)", borderRadius: "8px", background: "var(--pub-surface)", color: "var(--pub-ink)", font: "inherit" };
  const refCode = () => { try { return (new URLSearchParams(location.search).get("ref") || "").trim().toUpperCase().replace(/[^A-Z0-9_-]/g, "").slice(0, 30); } catch { return ""; } };

  P.register("shop", "storefront", async (root, data, ctx) => {
    const saved = loadTrade(ctx.token);
    if (saved && data.page.business_buyers && data.page.business_buyers.enabled) {
      try { const signed = await ctx.action("trade_sign_in", saved); storefront(root, { ...data, page: signed.page }, ctx, saved); return; } catch { saveTrade(ctx.token, null); }
    }
    storefront(root, data, ctx, null);
  });

  function storefront(root, data, ctx, signedIn) {
    const { h, money } = ctx;
    const page = data.page;
    const account = page.account || null;
    const buyers = page.business_buyers || { enabled: false };
    const pricesHidden = buyers.only && !account;
    const maxQty = account ? 100000 : 99;
    const products = page.products || [];
    const byId = Object.fromEntries(products.map((p) => [p.id, p]));
    let cart = loadCart(ctx.token, account).filter((line) => byId[line.product_id]);
    let category = "";
    let text = "";
    let quote = null;
    // Share and earn is on: a ?ref= link adds the friend's code (even at 0% off, so the sharer still earns).
    const friendCode = page.referrals || page.referral_percent ? refCode() : "";
    let discount = friendCode;
    let method = page.pickup.enabled ? "pickup" : "delivery";
    const trade = () => (signedIn ? { trade_email: signedIn.email, trade_code: signedIn.code } : {});
    const restart = (next, again) => { root.replaceChildren(); storefront(root, { ...data, page: next }, ctx, again); window.scrollTo({ top: 0, behavior: "smooth" }); };

    const grid = h("div", { class: "pub-grid" });
    const cartBox = h("section", { class: "pub-card", "aria-label": "Your cart" });
    const head = h("div", { class: "pub-card" },
      h("h1", { text: data.business.name || data.title || "Shop" }),
      data.business.tagline ? h("p", { class: "pub-muted", text: data.business.tagline }) : null,
      page.notice ? h("p", { class: "pub-notice", text: page.notice }) : null,
      !page.open ? h("p", { class: "pub-notice bad", text: page.closed_message }) : null,
      friendCode ? h("p", { class: "pub-notice good", text: page.referral_percent ? `A friend shared this shop with you: ${page.referral_percent}% off your first order. Their code ${friendCode} is added at checkout.` : `A friend shared this shop with you. Their code ${friendCode} is added at checkout, so they get a thank-you when you order.` }) : null,
      page.delivery.enabled && page.delivery.free_over_cents && !account ? h("p", { class: "pub-muted", text: `Free delivery on orders over ${money(page.delivery.free_over_cents)}.` }) : null);

    // ---------------------------------------------------------------- buying for a business
    function businessBox() {
      if (!buyers.enabled) return null;
      if (account) {
        const credit = account.credit || {};
        const signOut = ctx.button("Sign out", async () => { saveTrade(ctx.token, null); const fresh = await ctx.reload(); return fresh; }, "ghost");
        return h("section", { class: "pub-card", "aria-label": "Your trade account" },
          h("p", { class: "pub-badge good", text: "Trade prices" }),
          h("h2", { text: account.company }),
          h("p", { class: "pub-muted", text: [account.terms_label, account.discount_percent ? `${account.discount_percent}% off trade prices` : "", account.tax_exempt ? "no tax" : ""].filter(Boolean).join(" · ") }),
          credit.limit_cents ? h("p", { class: "pub-muted", text: `Credit: ${money(credit.available_cents || 0)} of ${money(credit.limit_cents)} available (${money(credit.open_cents || 0)} unpaid).` }) : null,
          buyers.min_order_cents ? h("p", { class: "pub-muted", text: `Business orders start at ${money(buyers.min_order_cents)}.` }) : null,
          h("div", { class: "pub-btn-row" }, signOut));
      }
      const signInBox = h("div", { class: "pub-form", hidden: true });
      const applyBox = h("div", { class: "pub-form", hidden: true });
      const email = h("input", { type: "email", autocomplete: "email" });
      const code = h("input", { autocomplete: "off", spellcheck: "false", placeholder: "ABCD-EFGH-JKLM", style: { textTransform: "uppercase" } });
      signInBox.append(ctx.field("Business email", email), ctx.field("Trade code", code, "From the email the shop sent when it approved your account."),
        h("div", { class: "pub-btn-row" }, ctx.button("Sign in", async () => {
          if (!email.value.trim() || !code.value.trim()) throw new Error("Enter your business email and trade code.");
          const creds = { email: email.value.trim(), code: code.value.trim() };
          const signed = await ctx.action("trade_sign_in", creds);
          saveTrade(ctx.token, creds);
          restart(signed.page, creds);
        })));
      const fields = {
        company: h("input", { autocomplete: "organization", required: true }),
        contact_name: h("input", { autocomplete: "name" }),
        email: h("input", { type: "email", autocomplete: "email", required: true }),
        phone: h("input", { type: "tel", autocomplete: "tel" }),
        tax_id: h("input", { placeholder: "VAT, GSTIN, EIN" }),
        address: h("textarea", { rows: 2, autocomplete: "street-address" }),
        note: h("textarea", { rows: 2, placeholder: "What you buy and how often" }),
      };
      applyBox.append(
        ctx.field("Business name", fields.company), ctx.field("Your name", fields.contact_name), ctx.field("Business email", fields.email, "Your trade code is sent here."),
        ctx.field("Phone", fields.phone), ctx.field("Tax or registration number", fields.tax_id), ctx.field("Delivery address", fields.address), ctx.field("Anything the shop should know", fields.note),
        h("div", { class: "pub-btn-row" }, ctx.button("Apply", async () => {
          if (!fields.company.value.trim()) { fields.company.focus(); throw new Error("Enter your business name."); }
          if (!fields.email.value.trim()) { fields.email.focus(); throw new Error("Enter your business email."); }
          const result = await ctx.action("trade_apply", Object.fromEntries(Object.entries(fields).map(([key, node]) => [key, node.value.trim()])));
          if (result.status === "approved" && result.code) {
            const creds = { email: fields.email.value.trim(), code: result.code };
            applyBox.replaceChildren(h("p", { class: "pub-notice good", role: "status", text: "You are approved. Keep this trade code private: anyone with it can order for your business." }), h("p", {}, h("span", { class: "pub-badge", text: result.code })),
              h("div", { class: "pub-btn-row" }, ctx.button("Copy the code", () => ctx.copy(result.code), "secondary"), ctx.button("Start ordering", async () => { const signed = await ctx.action("trade_sign_in", creds); saveTrade(ctx.token, creds); restart(signed.page, creds); })));
          } else {
            applyBox.replaceChildren(h("p", { class: "pub-notice good", role: "status", text: "Thanks! The shop will review your request and email your trade code when it is approved." }));
          }
        })));
      const toggle = (box, other) => () => { box.hidden = !box.hidden; other.hidden = true; const first = box.querySelector("input"); if (!box.hidden && first) first.focus(); };
      return h("section", { class: "pub-card", "aria-label": "Buying for a business" },
        h("h2", { text: buyers.only ? "Trade customers only" : "Buying for a business?" }),
        h("p", { class: "pub-muted", text: buyers.intro || (buyers.only ? "We sell to businesses. Sign in with your trade account to see prices and order, or apply for one." : "Get trade prices, buy in bulk and pay on account. Sign in with your trade code, or apply for a trade account.") }),
        h("div", { class: "pub-btn-row" },
          h("button", { type: "button", class: "pub-btn", text: "Sign in with a trade code", onclick: toggle(signInBox, applyBox) }),
          h("button", { type: "button", class: "pub-btn secondary", text: "Apply for a trade account", onclick: toggle(applyBox, signInBox) })),
        signInBox, applyBox);
    }

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
      if (existing) existing.quantity = Math.min(maxQty, existing.quantity + quantity);
      else cart.push({ product_id: product.id, option, quantity });
      saveCart(ctx.token, account, cart);
      renderCart();
      cartBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    // Sold out (or one option sold out): the customer leaves an email and hears when it is back.
    function notifyForm(product) {
      const soldOut = (product.options || []).filter((o) => !o.available);
      if (product.in_stock && !soldOut.length) return null;
      const box = h("div", { class: "pub-form", hidden: true });
      const toggle = h("button", { type: "button", class: "pub-btn ghost", "aria-expanded": "false", text: product.in_stock ? "Option sold out? Email me when it's back" : "Email me when it's back" });
      toggle.addEventListener("click", () => { box.hidden = !box.hidden; toggle.setAttribute("aria-expanded", String(!box.hidden)); if (!box.hidden) email.focus(); });
      const email = h("input", { type: "email", autocomplete: "email", required: true });
      const option = soldOut.length ? h("select", {}, ...soldOut.map((o) => h("option", { value: o.name, text: o.name }))) : null;
      const send = ctx.button("Tell me", async () => {
        if (!email.value.trim()) { email.focus(); throw new Error("Enter your email address."); }
        await ctx.action("notify_me", { product_id: product.id, option: option ? option.value : "", email: email.value.trim() });
        box.replaceChildren(h("p", { class: "pub-notice good", role: "status", text: "Thanks! We will email you when it is back." }));
        toggle.hidden = true;
      });
      box.append(...[option ? ctx.field("Which one", option) : null, ctx.field("Your email", email, "Only used to tell you it is back."), h("div", { class: "pub-btn-row" }, send)].filter(Boolean));
      return h("div", {}, toggle, box);
    }

    function priceLine(product) {
      if (product.price_cents === null || product.price_cents === undefined) return h("div", {}, h("span", { class: "pub-muted", text: "Trade price after sign-in" }));
      if (account) {
        return h("div", {},
          h("span", { class: "pub-price", text: money(product.price_cents) }), h("span", { class: "pub-muted", text: " each" }),
          product.retail_cents && product.retail_cents > product.price_cents ? h("s", { class: "pub-muted", text: ` ${money(product.retail_cents)}` }) : null);
      }
      return h("div", {}, h("span", { class: "pub-price", text: money(product.price_cents) }), product.compare_at_cents ? h("s", { class: "pub-muted", text: ` ${money(product.compare_at_cents)}` }) : null);
    }

    function card(product) {
      const options = product.options || [];
      const least = account ? Math.max(1, product.min_quantity || 1) : 1;
      const select = options.length ? h("select", { "aria-label": `Option for ${product.name}` }, ...options.map((o) => h("option", { value: o.name, disabled: !o.available, text: `${o.name}${o.price_cents !== null && o.price_cents !== product.price_cents ? ` · ${money(o.price_cents)}` : ""}${!o.available ? " (sold out)" : o.left ? ` (only ${o.left} left)` : ""}` }))) : null;
      if (select) { const firstAvailable = options.find((o) => o.available); if (firstAvailable) select.value = firstAvailable.name; }
      const quantity = h("input", { type: "number", min: least, max: maxQty, value: least, "aria-label": `Quantity of ${product.name}`, style: { ...INPUT, width: account ? "96px" : "76px" } });
      const details = h("p", { class: "pub-pre", text: product.description || "", hidden: true });
      const more = product.description ? h("button", { type: "button", class: "pub-btn ghost", text: "Details" }) : null;
      if (more) more.addEventListener("click", () => { details.hidden = !details.hidden; more.textContent = details.hidden ? "Details" : "Hide details"; });
      const add = h("button", { type: "button", class: "pub-btn", text: pricesHidden ? "Sign in to order" : product.in_stock ? "Add to cart" : "Sold out", disabled: pricesHidden || !product.in_stock || !page.open });
      add.addEventListener("click", () => addToCart(product, select ? select.value : "", Math.max(least, Math.min(maxQty, Number(quantity.value) || least))));
      const breaks = account && (product.price_breaks || []).length ? h("p", { class: "pub-muted", text: (product.price_breaks || []).map((b) => `${b.min_quantity}+ ${money(b.unit_cents)}`).join(" · ") }) : null;
      return h("article", { class: "pub-item" },
        product.images && product.images[0] ? h("img", { src: product.images[0], alt: product.name, loading: "lazy" }) : null,
        h("h3", { text: product.name }),
        priceLine(product),
        breaks,
        account && least > 1 ? h("span", { class: "pub-badge", text: `Minimum ${least}` }) : null,
        product.audience === "businesses" && account ? h("span", { class: "pub-badge", text: "Trade only" }) : null,
        product.left ? h("span", { class: "pub-badge bad", text: `Only ${product.left} left` }) : null,
        select ? ctx.field("Option", select) : null,
        h("div", { class: "pub-btn-row" }, product.in_stock && !pricesHidden ? quantity : null, add),
        notifyForm(product),
        more, details);
    }

    function renderGrid() {
      const visible = products.filter((p) => (!category || p.category === category) && (!text || `${p.name} ${p.description} ${p.category}`.toLowerCase().includes(text)));
      grid.replaceChildren(...(visible.length ? visible.map(card) : [h("p", { class: "pub-muted", text: products.length ? "Nothing matches. Try another search." : "No products yet. Check back soon." })]));
    }

    let timer = null;
    const requote = () => {
      clearTimeout(timer);
      if (!cart.length) { quote = null; renderTotals(); renderCartLines(); return; }
      timer = setTimeout(async () => {
        try { quote = (await ctx.action("quote", { items: cart, discount_code: discount, delivery_method: method, ...trade() })).quote; totalsError.textContent = ""; }
        catch (error) { quote = null; totalsError.textContent = error.message; }
        renderTotals();
        renderCartLines();
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
        quote.discount_cents ? row(`${quote.referral ? "Friend's code" : "Discount"} (${quote.discount_code})`, `-${money(quote.discount_cents)}`) : quote.referral ? row(`Friend's code (${quote.discount_code})`, "Added") : null,
        quote.delivery_method === "delivery" ? row("Delivery", quote.delivery_cents ? money(quote.delivery_cents) : "Free") : null,
        quote.tax_cents ? row(quote.tax_label, money(quote.tax_cents)) : null,
        row("Total", money(quote.total_cents), true),
        quote.buyer_type === "business" && quote.savings_cents ? row("You save on trade prices", money(quote.savings_cents)) : null,
        quote.buyer_type === "business" ? row("Payment", quote.terms === "prepay" ? quote.terms_label : `${quote.terms_label}, due ${ctx.date(quote.due_on)}`) : null)));
    }

    const name = h("input", { autocomplete: "name", required: true });
    const email = h("input", { type: "email", autocomplete: "email" });
    const phone = h("input", { type: "tel", autocomplete: "tel" });
    const address = h("textarea", { autocomplete: "street-address", rows: 3 });
    if (account && account.address) address.value = account.address;
    const note = h("textarea", { rows: 2, placeholder: "Anything the shop should know" });
    const po = h("input", { autocomplete: "off", maxlength: 60 });
    const code = h("input", { placeholder: "Discount code", "aria-label": "Discount code", value: discount, style: { ...INPUT, flex: "1 1 160px", maxWidth: "240px" } });
    const addressField = ctx.field("Delivery address", address);
    const linesBody = h("tbody");

    // A line's total comes from the shop's quote when there is one (quantity price breaks change the price each).
    function renderCartLines() {
      linesBody.replaceChildren(...cart.map((line, index) => {
        const product = byId[line.product_id];
        const option = (product.options || []).find((o) => o.name === line.option);
        const priced = quote && quote.lines.find((l) => l.product_id === line.product_id && (l.option || "") === (line.option || ""));
        const unit = priced ? priced.unit_cents : option && option.price_cents !== null ? option.price_cents : product.price_cents;
        const least = account ? Math.max(1, product.min_quantity || 1) : 1;
        const qty = h("input", { type: "number", min: least, max: maxQty, value: line.quantity, "aria-label": `Quantity of ${product.name}`, style: { ...INPUT, width: account ? "90px" : "70px" } });
        qty.addEventListener("change", () => { line.quantity = Math.max(1, Math.min(maxQty, Number(qty.value) || 1)); saveCart(ctx.token, account, cart); renderCart(); });
        const remove = h("button", { type: "button", class: "pub-btn ghost", text: "Remove" });
        remove.addEventListener("click", () => { cart.splice(index, 1); saveCart(ctx.token, account, cart); renderCart(); });
        return h("tr", {}, h("td", { text: `${product.name}${line.option ? ` (${line.option})` : ""}${priced && account ? ` · ${money(priced.unit_cents)} each` : ""}` }), h("td", {}, qty), h("td", { class: "num", text: money(unit * line.quantity) }), h("td", {}, remove));
      }));
    }

    function renderCart() {
      if (pricesHidden) {
        cartBox.replaceChildren(h("h2", { text: "Order" }), h("p", { class: "pub-muted", text: "Sign in with your trade account to see prices and order." }));
        return;
      }
      if (!cart.length) {
        cartBox.replaceChildren(h("h2", { text: "Your cart" }), h("p", { class: "pub-muted", text: "Your cart is empty." }));
        requote();
        return;
      }
      renderCartLines();
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
      const place = ctx.button(page.open ? (account ? "Place business order" : "Place order") : "The shop is closed", async () => {
        let payload;
        if (account) {
          if (buyers.require_po && !po.value.trim()) { po.focus(); throw new Error("Enter your purchase-order (PO) number."); }
          payload = { items: cart, discount_code: discount, delivery_method: method, address: method === "delivery" ? address.value : "", note: note.value, po_number: po.value.trim(), ...trade() };
        } else {
          if (!name.value.trim()) { name.focus(); throw new Error("Enter your name."); }
          if (!email.value.trim() && !phone.value.trim()) { email.focus(); throw new Error("Enter an email address or a phone number."); }
          payload = { items: cart, discount_code: discount, delivery_method: method, name: name.value, email: email.value, phone: phone.value, address: method === "delivery" ? address.value : "", note: note.value };
        }
        const result = await ctx.action("place_order", payload);
        cart = [];
        saveCart(ctx.token, account, cart);
        confirmation(result);
      });
      place.disabled = !page.open;
      const details = account
        ? h("div", { class: "pub-form" }, ctx.field(`PO number${buyers.require_po ? "" : " (optional)"}`, po, `Ordering as ${account.company} (${account.email}).`), addressField, ctx.field("Note", note))
        : h("div", { class: "pub-form" }, ctx.field("Your name", name), ctx.field("Email", email, "For your order confirmation"), ctx.field("Phone", phone), addressField, ctx.field("Note", note));
      // Native replaceChildren would print "null" for a missing part: keep only real nodes.
      cartBox.replaceChildren(...[
        h("h2", { text: `Your cart (${cart.reduce((sum, line) => sum + line.quantity, 0).toLocaleString()})` }),
        h("table", { class: "pub-table" }, linesBody),
        page.has_discounts ? h("div", { class: "pub-btn-row" }, code, apply) : null,
        page.pickup.enabled && page.pickup.instructions && method === "pickup" ? h("p", { class: "pub-muted", text: page.pickup.instructions }) : null,
        methods,
        totals, totalsError,
        details,
        h("div", { class: "pub-btn-row" }, place),
      ].filter(Boolean));
      requote();
    }

    function confirmation(result) {
      const order = result.order;
      const business = order.buyer_type === "business";
      const later = business && order.terms && order.terms !== "prepay";
      root.replaceChildren(...[
        h("section", { class: "pub-card" },
          h("p", { class: "pub-badge good", text: "Order placed" }),
          h("h1", { text: business ? `Thank you, ${order.company}!` : `Thank you, ${order.customer_name.split(" ")[0]}!` }),
          h("p", { text: `Your order number is #${order.number}${order.po_number ? ` (PO ${order.po_number})` : ""}. Total ${money(order.total_cents, order.currency)}.` }),
          later ? h("p", { class: "pub-notice", text: `${order.terms_label}: please pay by ${ctx.date(order.due_on)}. Your invoice is on your order page.` }) : null,
          h("p", { class: "pub-muted", text: order.delivery_method === "delivery" ? "We will let you know when it ships." : "We will let you know when it is ready to pick up." }),
          h("div", { class: "pub-btn-row" }, h("a", { class: "pub-btn secondary", href: result.link, text: business ? "See your order and invoice" : "See your order" }), ctx.button("Copy order link", () => ctx.copy(`${location.origin}${result.link}`), "ghost"))),
        order.total_cents ? ctx.paymentBox(order.total_cents, `#${order.number}`) : null,
      ].filter(Boolean));
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    renderChips();
    renderGrid();
    renderCart();
    root.append(...[head, businessBox(), products.length > 6 || page.categories.length ? h("div", { class: "pub-card" }, ctx.field("Search", search), page.categories.length ? chips : null) : null, grid, cartBox].filter(Boolean));
  }

  P.register("shop", "order", (root, data, ctx) => {
    const { h, money } = ctx;
    const order = data.page;
    const cancelled = order.fulfilment_status === "cancelled";
    const business = order.buyer_type === "business";
    const done = new Set((order.history || []).map((entry) => entry.status));
    const steps = order.steps.map((step) => {
      const at = (order.history || []).filter((entry) => entry.status === step).pop();
      const label = step === "delivered" && order.delivery_method === "pickup" ? "picked up" : step;
      return h("li", { class: done.has(step) || order.steps.indexOf(step) <= order.steps.indexOf(order.fulfilment_status) ? "pub-done" : "" }, h("strong", { text: `${done.has(step) || order.steps.indexOf(step) <= order.steps.indexOf(order.fulfilment_status) ? "✓ " : "○ "}${label[0].toUpperCase()}${label.slice(1)}` }), at ? h("span", { class: "pub-muted", text: ` ${ctx.datetime(at.at)}` }) : null);
    });
    const payment = { unpaid: [business && order.due_on ? `Due ${ctx.date(order.due_on)}` : "Waiting for payment", ""], paid: ["Paid", "good"], refunded: ["Refunded", "bad"] }[order.payment_status] || [order.payment_status, ""];
    const row = (label, value, strong) => h("tr", {}, h("td", { text: label }), h("td", { class: `num${strong ? " pub-total" : ""}`, text: value }));
    const reference = h("input", { placeholder: "Payment reference (optional)", "aria-label": "Payment reference" });
    root.append(
      h("section", { class: "pub-card" },
        h("h1", { text: business ? `Invoice for order #${order.number}` : `Order #${order.number}` }),
        h("div", { class: "pub-btn-row" }, h("span", { class: `pub-badge ${payment[1]}`, text: payment[0] }), cancelled ? h("span", { class: "pub-badge bad", text: "Cancelled" }) : null),
        h("p", { class: "pub-muted", text: `Placed ${ctx.datetime(order.created_at)} · ${order.delivery_method === "delivery" ? `Delivery to ${order.address}` : "Pickup"}` }),
        !cancelled ? h("ol", { class: "pub-form", style: { listStyle: "none", padding: "0" } }, ...steps) : null,
        order.tracking_url && /^https?:\/\//i.test(order.tracking_url) ? h("a", { class: "pub-btn secondary", href: order.tracking_url, target: "_blank", rel: "noopener noreferrer", text: "Track your parcel" }) : null),
      business ? invoiceCard(ctx, data.business || {}, order) : null,
      h("section", { class: "pub-card" },
        h("h2", { text: "Items" }),
        h("table", { class: "pub-table" }, h("tbody", {},
          ...order.items.map((line) => row(`${line.quantity.toLocaleString()} × ${line.name}${line.option ? ` (${line.option})` : ""}${business ? ` @ ${money(line.unit_cents, order.currency)}` : ""}`, money(line.line_cents, order.currency))),
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
    if (order.referral) root.append(shareCard(ctx, order.referral));
  });

  // A business order is its invoice: who sells, who buys, the tax numbers, the PO number and when it is due.
  function invoiceCard(ctx, seller, order) {
    const { h } = ctx;
    const pair = (label, value) => (value ? h("p", {}, h("strong", { text: `${label}: ` }), h("span", { text: value })) : null);
    return h("section", { class: "pub-card", "aria-label": "Invoice" },
      h("h2", { text: "Invoice" }),
      h("div", { class: "pub-grid" },
        h("div", {}, h("p", { class: "pub-muted", text: "From" }), h("p", {}, h("strong", { text: seller.name || "" })), seller.address ? h("p", { class: "pub-pre", text: seller.address }) : null, pair("Tax number", seller.tax_id)),
        h("div", {}, h("p", { class: "pub-muted", text: "Bill to" }), h("p", {}, h("strong", { text: order.company })), h("p", { text: order.customer_name }), pair("Tax number", order.tax_id))),
      pair("Invoice", `#${order.number}`),
      pair("Date", ctx.date(order.created_at.slice(0, 10))),
      pair("PO number", order.po_number),
      pair("Terms", order.terms_label),
      pair("Due", order.due_on ? ctx.date(order.due_on) : ""),
      h("div", { class: "pub-btn-row" }, h("button", { type: "button", class: "pub-btn secondary", text: "Print or save as PDF", onclick: () => window.print() })));
  }

  // Share and earn: the customer's own code, a link that adds it for friends, and what they have earned.
  function shareCard(ctx, share) {
    const { h, money } = ctx;
    const url = share.storefront_path ? `${location.origin}${share.storefront_path}?ref=${encodeURIComponent(share.code)}` : "";
    const joined = [
      share.friend_percent ? `friends get ${share.friend_percent}% off their first order` : "",
      share.reward_cents ? `you get ${money(share.reward_cents)} off your next order for each friend who buys` : "",
    ].filter(Boolean).join(", and ");
    const perks = joined ? `${joined[0].toUpperCase()}${joined.slice(1)}` : "";
    const shop = (ctx.business && ctx.business.name) || "this shop";
    const invite = share.friend_percent ? `Get ${share.friend_percent}% off your first order at ${shop} with my code ${share.code}.` : `Have a look at ${shop}. My code is ${share.code}.`;
    const actions = [
      url ? ctx.button("Copy your link", () => ctx.copy(url)) : null,
      ctx.button("Copy your code", () => ctx.copy(share.code), url ? "ghost" : ""),
      url && navigator.share ? ctx.button("Share", async () => { try { await navigator.share({ title: shop, text: invite, url }); } catch { /* the person closed the share sheet */ } }, "secondary") : null,
    ].filter(Boolean);
    return h("section", { class: "pub-card" },
      h("h2", { text: "Share and earn" }),
      perks ? h("p", { text: `${perks}.` }) : null,
      h("p", {}, "Your code ", h("span", { class: "pub-badge", text: share.code })),
      url ? h("p", { class: "pub-muted", style: { overflowWrap: "anywhere" }, text: url }) : null,
      h("div", { class: "pub-btn-row" }, ...actions),
      share.earned_cents ? h("p", { class: "pub-notice good", text: `You have earned ${money(share.earned_cents)} so far. Thank you for sharing!` }) : null);
  }
})();

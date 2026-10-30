/* Aira — Shop (#/shop): sell products online.
 *
 * Tabs (?tab=): overview (revenue, orders to fulfil, best sellers, low stock, the storefront link), orders (filters,
 * a drawer per order with payment and fulfilment actions and its customer link, orders entered by hand, CSV),
 * products (stock, options, images, an AI-written description, who is waiting for it to be back, CSV), discounts
 * (codes), customers (everyone who ordered, CSV), business buyers (B2B: who sells to businesses, trade accounts to
 * approve with payment terms, a percent off and a credit limit, trade codes, what businesses owe and reminders),
 * share and earn (customers' referral codes and the rewards they earn) and settings (open or closed, pickup,
 * delivery fee and free-delivery threshold, store notice). Products carry a trade price, a minimum quantity and
 * quantity price breaks for businesses; orders show the business, its PO number, terms and due date.
 * Everything runs through /api/shop/* (aira/suite/shop/shop_routes.py). The storefront and order pages customers
 * see are in public-shop.js. DOM is built with h() and text only.
 *
 * In the Aira frame (shell.js) this file speaks for all of Commerce: "+ New" (a new order, product, discount code,
 * booking or service), the search box across the top (products and orders from /api/shop/search; bookings,
 * customers and services from /api/bookings/search; Enter shows them all on #/shop?tab=search&q=) and the tabs as
 * folders in the left menu. A link with new=<kind>, order=<id> or product=<id> opens that form or record once.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const call = (path, opts) => S.api("shop", path, opts);

  const TABS = [
    { id: "overview", label: "Overview", icon: "dashboard" },
    { id: "orders", label: "Orders", icon: "applications" },
    { id: "products", label: "Products", icon: "shop" },
    { id: "discounts", label: "Discounts", icon: "events" },
    { id: "customers", label: "Customers", icon: "recruiters" },
    { id: "business", label: "Business buyers", icon: "careers" },
    { id: "referrals", label: "Share and earn", icon: "social" },
    { id: "settings", label: "Settings", icon: "settings" },
  ];
  const FULFILMENT = ["placed", "packed", "shipped", "delivered", "cancelled"];
  const PAYMENT = ["unpaid", "paid", "refunded"];
  const TONES = { placed: "warn", packed: "info", shipped: "info", delivered: "good", unpaid: "warn", paid: "good", refunded: "bad" };
  const TRADE_TONES = { pending: "warn", approved: "good", declined: "bad", paused: "" };
  const SELL_TO = [["consumers", "Consumers (B2C)"], ["businesses", "Businesses (B2B)"], ["both", "Businesses and consumers (B2B and B2C)"]];
  const AUDIENCE = [["all", "Everyone"], ["consumers", "Consumers only"], ["businesses", "Businesses only"]];
  const sellsToBusinesses = (business) => Boolean(business && business.sell_to && business.sell_to !== "consumers");
  const breaksText = (breaks) => (breaks || []).map((b) => `${b.min_quantity} | ${S.fromCents(b.unit_cents)}`).join("\n");
  function parseBreaks(text) {
    return String(text || "").split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
      const [quantity, priceText] = line.split("|").map((part) => part.trim());
      const unit = S.toCents(priceText || "");
      if (!Number(quantity) || unit === null) throw new Error(`Write each price break as quantity | price, like 10 | 9.00 (not "${line}").`);
      return { min_quantity: Number(quantity), unit_cents: unit };
    });
  }
  // The business name for a business order, the person for a consumer's.
  const buyerCell = (o) => (o.buyer_type === "business" ? h("span", {}, o.company || o.customer_name, " ", h("span", { class: "suite-pill info", text: "Business" })) : o.customer_name);
  const dueText = (o) => {
    if (o.buyer_type !== "business" || o.payment_status !== "unpaid" || !o.due_on) return "";
    const late = Math.round((Date.parse(new Date().toISOString().slice(0, 10)) - Date.parse(o.due_on)) / 86400000);
    return late > 0 ? `${late} day${late === 1 ? "" : "s"} late` : `due ${S.day(o.due_on)}`;
  };

  // A link from "+ New" or the search box opens a form or a record once (see the view at the end): what it asked
  // for waits here until the tab that owns it is drawn.
  const ONCE = ["new", "order", "product"];
  let pending = {};
  const take = (key, value) => { const found = pending[key]; if (!found || (value && found !== value)) return ""; delete pending[key]; return found; };
  const later = (fn) => { Promise.resolve().then(fn).catch((error) => console.warn(error)); };
  // On a phone a wide table keeps its key columns: cm-hide-<n> hides the nth column under 560px (app-commerce.css).
  const narrow = (table, ...columns) => h("div", { class: ["cm-narrow", ...columns.map((n) => `cm-hide-${n}`)].join(" ") }, table);

  const itemCount = (order) => (order.items || []).reduce((sum, line) => sum + Number(line.quantity || 0), 0);
  const optionsText = (options) => (options || []).map((o) => [o.name, o.stock === null || o.stock === undefined ? "" : o.stock, o.price_cents === null || o.price_cents === undefined ? "" : S.fromCents(o.price_cents)].join(" | ").replace(/( \| )+$/, "")).join("\n");
  function parseOptions(text) {
    return String(text || "").split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
      const [name, stock, priceText] = line.split("|").map((part) => part.trim());
      return { name, stock: stock === undefined || stock === "" ? null : Number(stock), price_cents: priceText === undefined || priceText === "" ? null : S.toCents(priceText) };
    });
  }

  // ------------------------------------------------------------------ overview
  async function overview(body, rerender) {
    const data = await call("/overview");
    const revenue = data.revenue;
    const parts = [];
    if (!data.products.total) parts.push(h("section", { class: "card cm-start" }, Aira.ui.empty("Add your first product", "Name, price and a photo are enough to start selling. Then share your storefront link.", S.button("Add a product", () => editProduct(null, rerender), { icon: "plus" }))));
    if (data.customer_says_paid) parts.push(h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: `${data.customer_says_paid} customer${data.customer_says_paid === 1 ? " says they have" : "s say they have"} paid` }), h("span", { class: "muted", text: "Check your payments and mark the orders paid." }), h("a", { class: "btn secondary sm", href: "#/shop?tab=orders", text: "See orders" })));
    if (data.trade && data.trade.pending) parts.push(h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: `${data.trade.pending} business${data.trade.pending === 1 ? " wants" : "es want"} a trade account` }), h("span", { class: "muted", text: "Approve them with payment terms so they can order at trade prices." }), h("a", { class: "btn secondary sm", href: "#/shop?tab=business", text: "Review" })));
    if (data.trade && data.trade.overdue_orders) parts.push(h("div", { class: "cloud-banner warn", role: "status" }, h("strong", { text: `${S.money(data.trade.overdue_cents, revenue.currency)} is overdue from businesses` }), h("span", { class: "muted", text: `${data.trade.overdue_orders} unpaid order${data.trade.overdue_orders === 1 ? " is" : "s are"} past the due date.` }), h("a", { class: "btn secondary sm", href: "#/shop?tab=business", text: "Send reminders" })));
    if (!data.settings.open) parts.push(h("div", { class: "cloud-banner warn", role: "status" }, h("strong", { text: "Your shop is closed" }), h("span", { class: "muted", text: "Customers can browse but not order." }), h("a", { class: "btn secondary sm", href: "#/shop?tab=settings", text: "Open it" })));
    parts.push(S.kpis([
      { label: "Revenue this month", value: S.money(revenue.this_month_cents, revenue.currency), hint: revenue.change_percent === null ? "" : `${revenue.change_percent >= 0 ? "+" : ""}${revenue.change_percent}% vs last month`, tone: "good" },
      { label: "Orders to fulfil", value: data.to_fulfil, tone: data.to_fulfil ? "warn" : "" },
      { label: "Waiting for payment", value: data.unpaid, tone: data.unpaid ? "warn" : "" },
      { label: "Average order", value: S.money(data.average_order_cents, revenue.currency) },
      { label: "Orders this month", value: data.orders_this_month },
    ]));
    if (data.sell_to && data.sell_to !== "consumers") {
      parts.push(S.kpis([
        { label: "Business sales this month (B2B)", value: S.money(data.buyers.business.this_month_cents, revenue.currency), hint: `${data.buyers.business.orders_this_month} order${data.buyers.business.orders_this_month === 1 ? "" : "s"}` },
        data.sell_to === "both" ? { label: "Consumer sales this month (B2C)", value: S.money(data.buyers.consumer.this_month_cents, revenue.currency), hint: `${data.buyers.consumer.orders_this_month} order${data.buyers.consumer.orders_this_month === 1 ? "" : "s"}` } : null,
        { label: "Owed by businesses", value: S.money(data.trade.open_cents, revenue.currency), tone: data.trade.overdue_cents ? "bad" : data.trade.open_cents ? "warn" : "", hint: data.trade.overdue_cents ? `${S.money(data.trade.overdue_cents, revenue.currency)} overdue` : "" },
        { label: "Trade accounts", value: data.trade.approved, hint: data.trade.pending ? `${data.trade.pending} to approve` : "" },
      ]));
    }
    parts.push(storefrontCard(data.storefront, rerender));
    parts.push(S.grid(
      S.section("Best sellers", S.table({ columns: [{ key: "name", label: "Product" }, { key: "quantity", label: "Sold", num: true }, { key: "revenue_cents", label: "Sales", num: true, render: (r) => S.money(r.revenue_cents) }], rows: data.best_sellers, empty: Aira.ui.empty("No sales yet", "Share your storefront to get your first order.") })),
      S.section("Low stock", data.low_stock.length ? S.table({ columns: [{ key: "name", label: "Product" }, { key: "stock", label: "Left", render: (r) => (r.options.length ? r.options.map((o) => `${o.name}: ${o.stock}`).join(", ") : String(r.stock)) }], rows: data.low_stock, onRow: (r) => editProduct(r.id, rerender) }) : Aira.ui.empty("Stock looks fine", "Products that track stock show here when they run low.")),
    ));
    if (data.wanted && data.wanted.length) {
      parts.push(S.section("Customers are waiting for",
        h("p", { class: "muted", text: "These are sold out and people asked to be told when they are back. Add stock and everyone waiting gets an email." }),
        S.table({ columns: [{ key: "name", label: "Product" }, { key: "waiting", label: "Waiting", num: true }], rows: data.wanted, onRow: (r) => editProduct(r.id, rerender) })));
    }
    parts.push(S.section("Recent orders", ordersTable(data.recent, rerender)));
    body.append(...parts);
  }

  function storefrontCard(link, rerender) {
    return S.share({
      title: "Your storefront",
      text: "Create the link to your shop and share it on social media, in WhatsApp or on your website. Customers order without an account.",
      link,
      onCreate: async () => (await call("/storefront/link", { method: "POST", body: {} })).link,
      onRevoke: async () => { await call("/storefront/link", { method: "DELETE" }); if (rerender) rerender(); },
    });
  }

  // ------------------------------------------------------------------ orders
  function ordersTable(rows, rerender, emptyAction = null) {
    return narrow(S.table({
      columns: [
        { key: "number", label: "Order", render: (o) => `#${o.number}` },
        { key: "created_at", label: "Placed", render: (o) => Aira.fmt.ago(o.created_at) },
        { key: "customer_name", label: "Customer", render: buyerCell },
        { key: "items", label: "Items", num: true, render: itemCount },
        { key: "total_cents", label: "Total", num: true, render: (o) => S.money(o.total_cents, o.currency) },
        { key: "payment_status", label: "Payment", render: (o) => h("span", {}, S.pill(o.payment_status, TONES), o.customer_says_paid_at && o.payment_status === "unpaid" ? h("span", { class: "muted small", text: " says paid" }) : null, dueText(o) ? h("span", { class: "muted small", text: ` ${dueText(o)}` }) : null) },
        { key: "fulfilment_status", label: "Status", render: (o) => S.pill(o.fulfilment_status, TONES) },
      ],
      rows,
      empty: Aira.ui.empty("No orders yet", "Orders from your storefront and the ones you enter yourself show here.", emptyAction),
      onRow: (order) => openOrder(order.id, rerender),
    }), 2, 4);
  }

  async function orders(body, route, rerender) {
    const query = route.query || {};
    const search = h("input", { type: "search", placeholder: "Search name, email, phone or #number", value: query.q || "", "aria-label": "Search orders" });
    const fulfilment = Aira.ui.select([["", "Any status"], ...S.statusOptions(FULFILMENT)], query.fulfilment || "", { "aria-label": "Status" });
    const payment = Aira.ui.select([["", "Any payment"], ...S.statusOptions(PAYMENT)], query.payment || "", { "aria-label": "Payment" });
    const business = await S.business();
    const buyer = Aira.ui.select([["", "Every buyer"], ["business", "Businesses (B2B)"], ["consumer", "Consumers (B2C)"]], query.buyer || "", { "aria-label": "Buyer" });
    const apply = () => Aira.router.go(["shop"], { tab: "orders", q: search.value.trim(), fulfilment: fulfilment.value, payment: payment.value, buyer: buyer.value });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") apply(); });
    fulfilment.addEventListener("change", apply);
    payment.addEventListener("change", apply);
    buyer.addEventListener("change", apply);
    const params = new URLSearchParams(Object.entries({ q: query.q || "", fulfilment: query.fulfilment || "", payment: query.payment || "", buyer: query.buyer || "" }).filter(([, v]) => v));
    const data = await call(`/orders${params.toString() ? `?${params}` : ""}`);
    body.append(
      h("div", { class: "btn-row cm-filters" }, search, fulfilment, payment, sellsToBusinesses(business) || query.buyer ? buyer : null,
        S.button("New order", () => newOrder(rerender)),
        S.button("Download CSV", () => Aira.download("/api/shop/orders?format=csv", "orders.csv"), { kind: "secondary" })),
      query.q || query.fulfilment || query.payment || query.buyer
        ? (data.orders.length ? ordersTable(data.orders, rerender) : Aira.ui.empty("No orders match", "Try a name, an email, a phone number or an order number like #1001.", h("a", { class: "btn secondary", href: "#/shop?tab=orders", text: "Clear the filters" })))
        : ordersTable(data.orders, rerender, S.button("Enter an order", () => newOrder(rerender), { kind: "secondary" })),
    );
    if (take("new", "order")) later(() => newOrder(rerender));
    const wanted = take("order");
    if (wanted) later(() => openOrder(wanted, rerender));
  }

  async function openOrder(orderId, rerender) {
    const { order } = await call(`/orders/${orderId}`);
    const body = h("div", { class: "suite-body" });
    const refresh = async () => { if (rerender) rerender(); await openOrder(orderId, rerender); };
    const act = (label, path, payload = {}, kind = "secondary sm") => S.button(label, async () => { await call(`/orders/${orderId}/${path}`, { method: "POST", body: payload }); Aira.toast(`Order #${order.number}: ${label.toLowerCase()}.`, "success"); await refresh(); }, { kind });
    const actions = [];
    const live = order.fulfilment_status !== "cancelled";
    // A refunded or cancelled order is not paid again (that would count its revenue twice): place a new order.
    if (order.payment_status === "unpaid" && live) actions.push(act("Mark paid", "paid", {}, ""));
    if (order.payment_status === "paid") actions.push(act("Mark unpaid", "unpaid", {}, "ghost sm"), S.button("Refund", async () => { if (await Aira.confirm({ title: "Refund this order", message: "Mark the order refunded? Its revenue stops counting. Send the money back through your payment provider yourself.", confirmLabel: "Mark refunded", danger: true })) { await call(`/orders/${orderId}/refund`, { method: "POST", body: {} }); await refresh(); } }, { kind: "ghost sm" }));
    if (live && order.fulfilment_status === "placed") actions.push(act("Packed", "fulfil", { status: "packed" }));
    if (live && ["placed", "packed"].includes(order.fulfilment_status)) actions.push(S.button("Shipped", async () => {
      const result = await S.edit({ title: `Ship order #${order.number}`, fields: [{ name: "tracking_url", label: "Tracking link", type: "url", placeholder: "https://", hint: "Optional. The customer gets it in the shipping email." }], values: { tracking_url: order.tracking_url }, submitLabel: "Mark shipped", onSave: (values) => call(`/orders/${orderId}/fulfil`, { method: "POST", body: { status: "shipped", tracking_url: values.tracking_url } }) });
      if (result) await refresh();
    }, { kind: "secondary sm" }));
    if (live && order.fulfilment_status !== "delivered") actions.push(act(order.delivery_method === "pickup" ? "Picked up" : "Delivered", "fulfil", { status: "delivered" }));
    if (order.payment_status === "unpaid" && live && order.customer_email) actions.push(S.button("Send payment reminder", async () => { await call(`/orders/${orderId}/remind`, { method: "POST", body: {} }); Aira.toast("The reminder waits in Business hub > Outbox until you approve it (or goes now if you send these automatically).", "success"); }, { kind: "secondary sm" }));
    if (live && order.fulfilment_status !== "delivered") actions.push(S.button("Cancel order", async () => { if (await Aira.confirm({ title: `Cancel order #${order.number}`, message: "Its stock and discount use come back. A paid order stays paid until you refund it.", confirmLabel: "Cancel order", danger: true })) { await call(`/orders/${orderId}/cancel`, { method: "POST", body: {} }); await refresh(); } }, { kind: "ghost sm" }));

    const lines = S.table({
      columns: [
        { key: "name", label: "Item", render: (l) => `${l.name}${l.option ? ` (${l.option})` : ""}` },
        { key: "quantity", label: "Qty", num: true },
        { key: "unit_cents", label: "Price", num: true, render: (l) => S.money(l.unit_cents, order.currency) },
        { key: "line_cents", label: "Total", num: true, render: (l) => S.money(l.line_cents, order.currency) },
      ],
      rows: order.items,
    });
    const totals = Aira.ui.kv([
      ["Subtotal", S.money(order.subtotal_cents, order.currency)],
      order.discount_cents ? [`Discount ${order.discount_code}`, `-${S.money(order.discount_cents, order.currency)}`] : ["", ""],
      order.delivery_cents ? ["Delivery", S.money(order.delivery_cents, order.currency)] : ["", ""],
      order.tax_cents ? ["Tax", S.money(order.tax_cents, order.currency)] : ["", ""],
      ["Total", S.money(order.total_cents, order.currency)],
    ]);
    const customer = Aira.ui.kv([
      ["Name", order.customer_name],
      ["Email", order.customer_email ? h("a", { href: `mailto:${order.customer_email}`, text: order.customer_email }) : ""],
      ["Phone", order.customer_phone ? h("a", { href: `tel:${order.customer_phone.replace(/[^0-9+]/g, "")}`, text: order.customer_phone }) : ""],
      ["Delivery", order.delivery_method === "delivery" ? `Delivery to ${order.address}` : "Pickup"],
      ["Note", order.note],
      ["Tracking", order.tracking_url ? h("a", { href: order.tracking_url, target: "_blank", rel: "noopener noreferrer", text: order.tracking_url }) : ""],
      ["Source", order.source === "manual" ? "Entered by the shop" : "Storefront"],
    ]);
    const businessBox = order.buyer_type === "business" ? S.section("Business",
      Aira.ui.kv([
        ["Business", order.trade_account_id ? h("a", { href: "#/shop?tab=business", text: order.company, onclick: (event) => { event.preventDefault(); Aira.closeDrawer(); openAccount(order.trade_account_id, rerender); } }) : order.company],
        ["Tax number", order.tax_id],
        ["PO number", order.po_number],
        ["Terms", order.terms_label || order.terms],
        ["Due", order.due_on ? `${S.day(order.due_on)}${dueText(order) ? ` (${dueText(order)})` : ""}` : ""],
      ]),
      h("div", { class: "btn-row" }, S.button("Change the PO or due date", async () => {
        const saved = await S.edit({ title: `Order #${order.number}`, fields: [{ name: "po_number", label: "PO number" }, { name: "due_on", label: "Due on", type: "date" }], values: order, onSave: (values) => call(`/orders/${orderId}`, { method: "PATCH", body: values }) });
        if (saved) await refresh();
      }, { kind: "ghost sm" }))) : null;
    const notes = h("textarea", { rows: 3, placeholder: "Only your team sees these notes", "aria-label": "Internal notes" });
    notes.value = order.internal_notes || "";
    const history = h("ol", { class: "suite-checklist" }, ...(order.history || []).slice().reverse().map((entry) => h("li", { class: "done" }, h("span", { class: "suite-tick", "aria-hidden": "true", text: "✓" }), h("span", {}, h("strong", { text: String(entry.status || "").replaceAll("_", " ") }), h("span", { class: "muted", text: ` ${S.when(entry.at)}${entry.note ? ` · ${entry.note}` : ""}` })))));
    body.append(
      h("div", { class: "btn-row" }, S.pill(order.payment_status, TONES), S.pill(order.fulfilment_status, TONES), order.customer_says_paid_at && order.payment_status === "unpaid" ? h("span", { class: "muted", text: `Customer says paid ${Aira.fmt.ago(order.customer_says_paid_at)}` }) : null),
      h("div", { class: "btn-row" }, ...actions),
      S.section("Items", lines, totals),
      businessBox,
      S.section("Customer", customer),
      S.share({ title: order.buyer_type === "business" ? "Order and invoice page" : "Customer's order page", text: "Shows the order, its status and how to pay.", link: order.link, onCreate: async () => (await call(`/orders/${orderId}/link`, { method: "POST", body: {} })).link, onRevoke: async () => { await call(`/orders/${orderId}/link`, { method: "DELETE" }); } }),
      S.section("Notes", notes, h("div", { class: "btn-row" }, S.button("Save notes", async () => { await call(`/orders/${orderId}`, { method: "PATCH", body: { internal_notes: notes.value } }); Aira.toast("Saved.", "success"); }, { kind: "secondary sm" }))),
      S.section("History", history),
    );
    Aira.drawer({ title: `Order #${order.number}`, subtitle: `${S.money(order.total_cents, order.currency)} · ${S.when(order.created_at)}`, body });
  }

  async function newOrder(rerender, forAccount = "") {
    const { products } = await call("/products");
    const choices = products.filter((p) => p.status !== "archived");
    if (!choices.length) { Aira.toast("Add a product first.", "error"); return; }
    const business = await S.business();
    const accounts = sellsToBusinesses(business) || forAccount ? (await call("/trade-accounts?status=approved")).accounts : [];
    const lines = [];
    const list = h("div", { class: "suite-form" });
    const quoteBox = h("div", { class: "muted" });
    const buyer = accounts.length ? S.form([
      { name: "trade_account_id", label: "Buyer", type: "select", options: [["", "A consumer (enter their details)"], ...accounts.map((a) => [a.id, `${a.company} · ${a.terms_label}`])], hint: "A business buys at its trade prices, on its terms." },
      { name: "po_number", label: "PO number" },
    ], { trade_account_id: forAccount }) : null;
    const form = S.form([
      { type: "heading", label: "Customer" },
      { name: "name", label: "Name", required: true },
      { name: "email", label: "Email", type: "email" },
      { name: "phone", label: "Phone", type: "tel" },
      { name: "delivery_method", label: "Delivery", type: "select", options: [["pickup", "Pickup"], ["delivery", "Delivery"]] },
      { name: "address", label: "Delivery address", type: "textarea", rows: 2 },
      { name: "discount_code", label: "Discount code" },
      { name: "note", label: "Note", type: "textarea", rows: 2 },
      { name: "mark_paid", label: "Already paid", type: "checkbox" },
    ]);
    const payloadItems = () => lines.map((line) => ({ product_id: line.product.value, option: line.option.value, quantity: Number(line.quantity.value || 1) }));
    const buyerValues = () => (buyer ? buyer.read() : {});
    // A business's contact details come from its trade account: the consumer fields hide.
    const showCustomer = () => {
      const isBusiness = Boolean(buyerValues().trade_account_id);
      for (const name of ["name", "email", "phone"]) { const field = form.inputs[name].control.closest(".field"); if (field) field.hidden = isBusiness; }
      if (buyer) { const po = buyer.inputs.po_number.control.closest(".field"); if (po) po.hidden = !isBusiness; }
    };
    let timer = null;
    const requote = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const values = form.read();
        try {
          const quote = await call("/quote", { method: "POST", body: { items: payloadItems(), discount_code: values.discount_code, delivery_method: values.delivery_method, trade_account_id: buyerValues().trade_account_id || "" }, silent: true });
          quoteBox.textContent = `Subtotal ${S.money(quote.subtotal_cents)}${quote.discount_cents ? ` · discount -${S.money(quote.discount_cents)}` : ""}${quote.delivery_cents ? ` · delivery ${S.money(quote.delivery_cents)}` : ""}${quote.tax_cents ? ` · ${quote.tax_label} ${S.money(quote.tax_cents)}` : ""} · Total ${S.money(quote.total_cents)}${quote.buyer_type === "business" ? ` · ${quote.terms_label}, due ${S.day(quote.due_on)}${quote.savings_cents ? ` · saves ${S.money(quote.savings_cents)}` : ""}` : ""}`;
        } catch (error) { quoteBox.textContent = error.message; }
      }, 250);
    };
    const addLine = () => {
      const product = Aira.ui.select(choices.map((p) => [p.id, `${p.name} · ${S.money(p.price_cents)}${p.status === "draft" ? " (draft)" : ""}`]), choices[0].id, { "aria-label": "Product" });
      const option = h("select", { "aria-label": "Option" });
      const quantity = h("input", { type: "number", min: 1, max: accounts.length ? 100000 : 99, value: 1, "aria-label": "Quantity", style: { width: "90px" } });
      const fillOptions = () => {
        const chosen = choices.find((p) => p.id === product.value);
        option.replaceChildren(...((chosen && chosen.options.length) ? chosen.options.map((o) => h("option", { value: o.name, text: o.name })) : [h("option", { value: "", text: "No options" })]));
        option.disabled = !(chosen && chosen.options.length);
      };
      fillOptions();
      const line = { product, option, quantity };
      const row = h("div", { class: "btn-row cm-line" }, product, option, quantity, h("button", { type: "button", class: "btn ghost sm", text: "Remove", onclick: () => { lines.splice(lines.indexOf(line), 1); row.remove(); requote(); } }));
      product.addEventListener("change", () => { fillOptions(); requote(); });
      option.addEventListener("change", requote);
      quantity.addEventListener("input", requote);
      lines.push(line);
      list.append(row);
      requote();
    };
    addLine();
    form.node.addEventListener("change", requote);
    if (buyer) buyer.node.addEventListener("change", () => { showCustomer(); requote(); });
    showCustomer();
    Aira.modal({
      title: "New order",
      wide: true,
      body: h("div", { class: "suite-body" }, buyer ? buyer.node : null, h("h3", { class: "suite-h3", text: "Items" }), list, h("div", {}, h("button", { type: "button", class: "btn secondary sm", text: "Add an item", onclick: addLine })), quoteBox, form.node),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Create order", kind: "primary", onClick: async () => {
          form.setError("");
          try {
            const { order } = await call("/orders", { method: "POST", body: { ...form.read(), ...buyerValues(), items: payloadItems() } });
            Aira.toast(`Order #${order.number} created.`, "success");
            if (rerender) rerender();
            return true;
          } catch (error) { form.setError(error.message); return false; }
        } },
      ],
    });
  }

  // ------------------------------------------------------------------ products
  async function productsTab(body, route, rerender) {
    const query = route.query || {};
    const search = h("input", { type: "search", placeholder: "Search products", value: query.q || "", "aria-label": "Search products" });
    const status = Aira.ui.select([["", "Any status"], ["active", "Active"], ["draft", "Draft"], ["archived", "Archived"]], query.status || "", { "aria-label": "Status" });
    const params = new URLSearchParams(Object.entries({ q: query.q || "", status: query.status || "", category: query.category || "" }).filter(([, v]) => v));
    const data = await call(`/products${params.toString() ? `?${params}` : ""}`);
    const category = Aira.ui.select([["", "Any category"], ...data.categories.map((c) => [c, c])], query.category || "", { "aria-label": "Category" });
    const apply = () => Aira.router.go(["shop"], { tab: "products", q: search.value.trim(), status: status.value, category: category.value });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") apply(); });
    status.addEventListener("change", apply);
    category.addEventListener("change", apply);
    body.append(
      h("div", { class: "btn-row cm-filters" }, search, status, category, S.button("Add a product", () => editProduct(null, rerender)), S.button("Download CSV", () => Aira.download("/api/shop/products?format=csv", "products.csv"), { kind: "secondary" })),
      narrow(S.table({
        columns: [
          { key: "name", label: "Product", render: (p) => h("span", { class: "cm-product" }, p.images && p.images[0] ? h("img", { src: p.images[0], alt: "", loading: "lazy" }) : null, h("span", { text: p.name })) },
          { key: "category", label: "Category" },
          { key: "price_cents", label: "Price", num: true, render: (p) => h("span", {}, S.money(p.price_cents), p.wholesale_cents ? h("span", { class: "muted small", text: ` · trade ${S.money(p.wholesale_cents)}` }) : null, p.audience && p.audience !== "all" ? h("span", {}, " ", h("span", { class: "suite-pill", text: p.audience === "businesses" ? "B2B only" : "B2C only" })) : null) },
          { key: "stock", label: "Stock", render: (p) => (!p.track_stock ? "Not tracked" : h("span", {}, p.options.some((o) => o.stock !== null) ? p.options.filter((o) => o.stock !== null).map((o) => `${o.name}: ${o.stock}`).join(", ") : String(p.stock), p.low ? h("span", {}, " ", S.pill("low", { low: "bad" })) : null, p.waiting ? h("span", {}, " ", h("span", { class: "suite-pill info", text: `${p.waiting} waiting` })) : null)) },
          { key: "status", label: "Status", render: (p) => S.pill(p.status, { active: "good", draft: "", archived: "" }) },
        ],
        rows: data.products,
        empty: query.q || query.status || query.category
          ? Aira.ui.empty("No products match", "Try other words, or clear the filters.", h("a", { class: "btn secondary", href: "#/shop?tab=products", text: "Clear the filters" }))
          : Aira.ui.empty("No products yet", "Add what you sell: a name, a price and a photo are enough.", S.button("Add a product", () => editProduct(null, rerender))),
        onRow: (p) => editProduct(p.id, rerender),
      }), 2),
    );
    if (take("new", "product")) later(() => editProduct(null, rerender));
    const wanted = take("product");
    if (wanted) later(() => editProduct(wanted, rerender));
  }

  async function editProduct(productId, rerender) {
    const product = productId ? (await call(`/products/${productId}`)).product : { status: "active", track_stock: false, stock: 0, low_stock: 3, options: [], images: [], audience: "all", min_quantity: 1, price_breaks: [] };
    const business = await S.business();
    const form = S.form([
      { name: "name", label: "Name", required: true },
      { name: "price_cents", label: "Price", type: "money", required: true },
      { name: "compare_at_cents", label: "Was (compare-at) price", type: "money", hint: "Optional. Shown crossed out when higher than the price." },
      { name: "category", label: "Category", placeholder: "Mugs, T-shirts, Gifts" },
      { name: "description", label: "Description", type: "textarea", rows: 6 },
      { name: "images_text", label: "Photos (one https link per line)", type: "textarea", rows: 3 },
      { name: "sku", label: "SKU" },
      { name: "status", label: "Status", type: "select", options: [["active", "Active (on the storefront)"], ["draft", "Draft (hidden)"], ["archived", "Archived"]] },
      { name: "track_stock", label: "Track stock", type: "checkbox" },
      { name: "stock", label: "Stock", type: "number", min: 0 },
      { name: "low_stock", label: "Low-stock alert at", type: "number", min: 0 },
      { name: "options_text", label: "Options (one per line: name | stock | price)", type: "textarea", rows: 4, hint: "For example: Small | 5 | 18.00. Stock and price are optional; without a price an option costs the product price." },
      ...(sellsToBusinesses(business) ? [
        { type: "heading", label: "Selling to businesses (B2B)" },
        { name: "audience", label: "Who can buy it", type: "select", options: AUDIENCE },
        { name: "wholesale_cents", label: "Trade price", type: "money", hint: "What a business with a trade account pays. Empty: the same as the price." },
        { name: "min_quantity", label: "Minimum quantity for businesses", type: "number", min: 1 },
        { name: "breaks_text", label: "Price breaks (one per line: quantity | price each)", type: "textarea", rows: 3, hint: "For example: 10 | 9.00 and 50 | 8.00. A business ordering that many pays that much each." },
      ] : []),
    ], { ...product, images_text: (product.images || []).join("\n"), options_text: optionsText(product.options), breaks_text: breaksText(product.price_breaks), wholesale_cents: product.wholesale_cents || null });
    const write = S.button("Write the description for me", async () => {
      const values = form.read();
      if (!values.name) { form.setError("Enter the name first."); return; }
      const draft = await call("/products/draft", { method: "POST", body: { name: values.name, category: values.category, notes: values.description } });
      form.inputs.description.control.value = draft.description;
      Aira.toast(draft.source === "model" ? "Written by the writing model. Read it before you save." : "A starting point. Make it yours.", "success");
    }, { kind: "secondary sm" });
    const save = S.button(productId ? "Save" : "Add product", async () => {
      form.setError("");
      const values = form.read();
      const payload = { ...values, images: String(values.images_text || "").split(/\s+/).filter(Boolean), options: parseOptions(values.options_text) };
      delete payload.images_text;
      delete payload.options_text;
      if ("breaks_text" in values) {
        try { payload.price_breaks = parseBreaks(values.breaks_text); } catch (error) { form.setError(error.message); return; }
        delete payload.breaks_text;
        payload.wholesale_cents = values.wholesale_cents || 0;
        payload.min_quantity = values.min_quantity || 1;
      }
      try {
        if (productId) await call(`/products/${productId}`, { method: "PATCH", body: payload });
        else await call("/products", { method: "POST", body: payload });
        Aira.toast("Saved.", "success");
        Aira.closeDrawer();
        if (rerender) rerender();
      } catch (error) { form.setError(error.message); }
    });
    const remove = productId ? S.button("Delete", async () => {
      if (!(await S.confirmDelete(product.name))) return;
      const result = await call(`/products/${productId}`, { method: "DELETE" });
      Aira.toast(result.archived ? "It has orders, so it was archived instead." : "Deleted.", "success");
      Aira.closeDrawer();
      if (rerender) rerender();
    }, { kind: "ghost" }) : null;
    const alerts = product.alerts || [];
    const waitingCard = productId ? S.section("Waiting for it to be back",
      h("p", { class: "muted", text: "Customers ask for this on your storefront when it is sold out. When you add stock, everyone waiting gets an email (it waits in Business hub > Outbox until you approve it)." }),
      S.table({
        columns: [
          { key: "email", label: "Customer", render: (a) => `${a.name ? `${a.name} · ` : ""}${a.email}` },
          { key: "option", label: "Option" },
          { key: "created_at", label: "Asked", render: (a) => Aira.fmt.ago(a.created_at) },
          { key: "notified_at", label: "Status", render: (a) => (a.notified_at ? S.pill("told", { told: "good" }) : S.pill("waiting", { waiting: "info" })) },
          { key: "x", label: "", render: (a) => S.button("Remove", async () => { await call(`/alerts/${a.id}`, { method: "DELETE" }); Aira.closeDrawer(); await editProduct(productId, rerender); }, { kind: "ghost sm" }) },
        ],
        rows: alerts,
        empty: Aira.ui.empty("Nobody is waiting", "When this is sold out, customers can ask to be told when it is back."),
      })) : null;
    Aira.drawer({ title: productId ? product.name : "New product", body: h("div", { class: "suite-body" }, form.node, h("div", { class: "btn-row" }, write), h("div", { class: "btn-row" }, save, remove), waitingCard) });
  }

  // ------------------------------------------------------------------ discounts
  async function discountsTab(body, rerender) {
    const data = await call("/discounts");
    const fields = [
      { name: "code", label: "Code", required: true, placeholder: "WELCOME10" },
      { name: "kind", label: "Kind", type: "select", options: [["percent", "Percent off"], ["fixed", "Amount off"]] },
      { name: "value", label: "Percent (for percent off) or amount (for amount off)", required: true, hint: "10 means 10% for percent off; 5.00 means 5.00 off for amount off." },
      { name: "min_order_cents", label: "Minimum order", type: "money" },
      { name: "expires_on", label: "Expires on", type: "date" },
      { name: "usage_limit", label: "Use limit (0 = no limit)", type: "number", min: 0 },
      { name: "active", label: "Active", type: "checkbox" },
    ];
    const toPayload = (values) => ({ ...values, value: values.kind === "fixed" ? S.toCents(values.value) : Number(values.value), usage_limit: values.usage_limit || 0 });
    const describe = (d) => (d.kind === "percent" ? `${d.value}% off` : `${S.money(d.value)} off`);
    const edit = async (discount) => {
      const values = discount ? { ...discount, value: discount.kind === "fixed" ? S.fromCents(discount.value) : discount.value } : { kind: "percent", active: true, usage_limit: 0 };
      const saved = await S.edit({ title: discount ? `Code ${discount.code}` : "New discount code", fields, values, onSave: (v) => (discount ? call(`/discounts/${discount.id}`, { method: "PATCH", body: toPayload(v) }) : call("/discounts", { method: "POST", body: toPayload(v) })) });
      if (saved) rerender();
    };
    body.append(
      h("div", { class: "btn-row" }, S.button("New discount code", () => edit(null))),
      S.table({
        columns: [
          { key: "code", label: "Code" },
          { key: "value", label: "Discount", render: describe },
          { key: "min_order_cents", label: "Minimum", render: (d) => (d.min_order_cents ? S.money(d.min_order_cents) : "") },
          { key: "expires_on", label: "Expires", render: (d) => (d.expires_on ? S.day(d.expires_on) : "Never") },
          { key: "uses", label: "Used", num: true, render: (d) => (d.usage_limit ? `${d.uses} / ${d.usage_limit}` : String(d.uses)) },
          { key: "active", label: "Status", render: (d) => S.pill(d.active ? "active" : "off", { off: "" }) },
          { key: "id", label: "", render: (d) => h("button", { type: "button", class: "btn ghost sm", text: "Delete", onclick: async () => { if (await S.confirmDelete(`code ${d.code}`)) { await call(`/discounts/${d.id}`, { method: "DELETE" }); rerender(); } } }) },
        ],
        rows: data.discounts,
        empty: Aira.ui.empty("No discount codes", "A code like WELCOME10 brings first-time customers back. Share it with your storefront link.", S.button("Create a code", () => edit(null), { kind: "secondary" })),
        onRow: edit,
      }),
    );
    if (take("new", "discount")) later(() => edit(null));
  }

  // ------------------------------------------------------------------ customers
  async function customersTab(body, route) {
    const query = route.query || {};
    const search = h("input", { type: "search", placeholder: "Search customers", value: query.q || "", "aria-label": "Search customers" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") Aira.router.go(["shop"], { tab: "customers", q: search.value.trim() }); });
    const data = await call(`/customers${query.q ? `?q=${encodeURIComponent(query.q)}` : ""}`);
    const repeat = data.customers.filter((c) => c.repeat).length;
    body.append(
      S.kpis([{ label: "Customers", value: data.customers.length }, { label: "Came back", value: repeat, hint: data.customers.length ? `${Math.round((repeat * 100) / data.customers.length)}% ordered again` : "" }]),
      h("div", { class: "btn-row cm-filters" }, search, S.button("Download CSV", () => Aira.download("/api/shop/customers?format=csv", "customers.csv"), { kind: "secondary" })),
      narrow(S.table({
        columns: [
          { key: "name", label: "Name", render: (c) => h("span", {}, c.buyer_type === "business" && c.company ? `${c.company} (${c.name})` : c.name, c.buyer_type === "business" ? h("span", {}, " ", h("span", { class: "suite-pill info", text: "Business" })) : null, c.repeat ? h("span", {}, " ", S.pill("repeat", { repeat: "good" })) : null) },
          { key: "email", label: "Email" },
          { key: "phone", label: "Phone" },
          { key: "orders", label: "Orders", num: true },
          { key: "spend_cents", label: "Paid", num: true, render: (c) => S.money(c.spend_cents) },
          { key: "last_order_at", label: "Last order", render: (c) => Aira.fmt.ago(c.last_order_at) },
        ],
        rows: data.customers,
        empty: query.q
          ? Aira.ui.empty("No customers match", "Try a name, an email or a phone number.", h("a", { class: "btn secondary", href: "#/shop?tab=customers", text: "Show everyone" }))
          : Aira.ui.empty("No customers yet", "Everyone who orders shows here, with what they spent. Share your storefront to get the first order.", h("a", { class: "btn secondary", href: "#/shop?tab=overview", text: "Share your storefront" })),
        onRow: (c) => Aira.router.go(["shop"], { tab: "orders", q: c.email || c.phone || c.name }),
      }), 3, 6),
    );
  }

  // ------------------------------------------------------------------ business buyers (B2B)
  async function sellTo(value, rerender) {
    await Aira.api("/api/suite/business", { method: "POST", body: { sell_to: value } });
    await S.business(true);
    Aira.toast(value === "consumers" ? "You sell to consumers." : value === "businesses" ? "You sell to businesses." : "You sell to businesses and consumers.", "success");
    if (rerender) rerender();
  }

  // The trade code is shown once, when it is made: the shop passes it on (it is also emailed).
  function showCode(made) {
    Aira.modal({
      title: `Trade code for ${made.account.company}`,
      body: h("div", { class: "suite-body" },
        h("p", { text: "The business signs in on your storefront with its email and this code to see your trade prices and order on its terms. It is shown only now; you can make a new one at any time." }),
        h("p", {}, h("code", { class: "cm-code", text: made.code })),
        h("p", { class: "muted", text: `It is also in an email to ${made.account.email}, waiting in Business hub > Outbox until you approve it (unless you send these automatically).` })),
      actions: [
        { label: "Copy code", kind: "secondary", onClick: async () => { await S.copy(made.code); return false; } },
        { label: "Done", kind: "primary" },
      ],
    });
  }

  const accountFields = (terms) => [
    { type: "heading", label: "Business" },
    { name: "company", label: "Business name", required: true },
    { name: "contact_name", label: "Contact name" },
    { name: "email", label: "Email", type: "email", required: true, hint: "They sign in on your storefront with this email and their trade code." },
    { name: "phone", label: "Phone", type: "tel" },
    { name: "tax_id", label: "Tax or registration number", placeholder: "VAT, GSTIN, EIN" },
    { name: "address", label: "Address", type: "textarea", rows: 2, hint: "Orders are delivered here unless they give another address." },
    { type: "heading", label: "Terms" },
    { name: "terms", label: "Payment terms", type: "select", options: terms.map((t) => [t.id, t.label]) },
    { name: "discount_percent", label: "Percent off your trade prices", type: "number", min: 0, max: 90 },
    { name: "credit_limit_cents", label: "Credit limit", type: "money", hint: "The most they may owe at once when they pay later. Empty for no limit." },
    { name: "tax_exempt", label: "Charge no tax (a reseller with a tax certificate)", type: "checkbox" },
    { name: "note", label: "Notes (only your team sees them)", type: "textarea", rows: 2 },
  ];

  async function openAccount(accountId, rerender, terms = null) {
    const termsList = terms || (await call("/trade-accounts?status=approved")).terms;
    const { account, credit, orders: placed } = await call(`/trade-accounts/${accountId}`);
    const form = S.form(accountFields(termsList), { ...account, credit_limit_cents: account.credit_limit_cents || null });
    const refresh = async () => { Aira.closeDrawer(); if (rerender) rerender(); await openAccount(accountId, rerender, termsList); };
    const values = () => ({ ...form.read(), credit_limit_cents: form.read().credit_limit_cents || 0 });
    const save = async () => { await call(`/trade-accounts/${accountId}`, { method: "PATCH", body: values() }); };
    const actions = [];
    if (account.status !== "approved") actions.push(S.button(account.status === "pending" ? "Approve" : "Approve again (new code)", async () => {
      form.setError("");
      try { await save(); const made = await call(`/trade-accounts/${accountId}/approve`, { method: "POST", body: {} }); await refresh(); showCode(made); } catch (error) { form.setError(error.message); }
    }));
    if (account.status === "approved") actions.push(S.button("Save", async () => { form.setError(""); try { await save(); Aira.toast("Saved.", "success"); await refresh(); } catch (error) { form.setError(error.message); } }));
    if (account.status === "pending") actions.push(S.button("Decline", async () => { await call(`/trade-accounts/${accountId}/decline`, { method: "POST", body: {} }); await refresh(); }, { kind: "ghost" }));
    if (account.status === "approved") {
      actions.push(S.button("New order for them", () => { Aira.closeDrawer(); newOrder(rerender, accountId); }, { kind: "secondary" }));
      actions.push(S.button("New trade code", async () => { if (await Aira.confirm({ title: "Make a new trade code", message: "The old code stops working at once. The new one is emailed to them and shown to you.", confirmLabel: "Make a new code" })) { const made = await call(`/trade-accounts/${accountId}/code`, { method: "POST", body: {} }); showCode(made); } }, { kind: "ghost" }));
      actions.push(S.button("Pause", async () => { await call(`/trade-accounts/${accountId}`, { method: "PATCH", body: { status: "paused" } }); await refresh(); }, { kind: "ghost" }));
    }
    actions.push(S.button("Delete", async () => {
      if (!(await S.confirmDelete(account.company))) return;
      const result = await call(`/trade-accounts/${accountId}`, { method: "DELETE" });
      Aira.toast(result.paused ? "It has orders, so it was paused instead." : "Deleted.", "success");
      Aira.closeDrawer();
      if (rerender) rerender();
    }, { kind: "ghost" }));
    const currency = (await S.business()).currency;
    Aira.drawer({
      title: account.company,
      subtitle: `${account.status} · ${account.terms_label}`,
      body: h("div", { class: "suite-body" },
        h("div", { class: "btn-row" }, S.pill(account.status, TRADE_TONES), account.source === "storefront" ? h("span", { class: "muted", text: `Applied on your storefront ${Aira.fmt.ago(account.created_at)}` }) : null),
        S.kpis([
          { label: "Owed now", value: S.money(credit.open_cents, currency), tone: credit.open_cents ? "warn" : "" },
          { label: "Credit limit", value: credit.limit_cents ? S.money(credit.limit_cents, currency) : "No limit" },
          credit.available_cents !== null ? { label: "Can still order on terms", value: S.money(credit.available_cents, currency) } : null,
        ]),
        form.node,
        h("div", { class: "btn-row" }, ...actions),
        S.section("Their orders", ordersTable(placed, rerender))),
    });
  }

  async function addBusiness(rerender, terms) {
    const made = await S.edit({
      title: "Add a business buyer",
      wide: true,
      fields: [...accountFields(terms), { name: "send", label: "Email them their trade code", type: "checkbox" }],
      values: { terms: terms[0] && terms[0].id, send: true },
      submitLabel: "Add and approve",
      onSave: (values) => call("/trade-accounts", { method: "POST", body: { ...values, credit_limit_cents: values.credit_limit_cents || 0 } }),
    });
    if (made) { if (rerender) rerender(); showCode(made); }
  }

  async function businessTab(body, route, rerender) {
    const query = route.query || {};
    const data = await call("/trade-accounts");
    if (data.sell_to === "consumers") {
      body.append(S.section("Sell to businesses too",
        h("p", { class: "muted cm-lead", text: "Cafes, shops, offices and resellers buy in bulk and come back every month. Let businesses apply for a trade account on your storefront. You approve each one with payment terms (pay now, or within 7 to 60 days), a percent off and a credit limit. They see your trade prices, minimum quantities and price breaks, and pay by your invoice; consumers keep the checkout they have." }),
        h("div", { class: "btn-row" },
          S.button("Sell to businesses and consumers", () => sellTo("both", rerender)),
          S.button("Sell only to businesses", () => sellTo("businesses", rerender), { kind: "secondary" }))));
      return;
    }
    const owed = await call("/receivables");
    const currency = (await S.business()).currency;
    const count = (status) => data.accounts.filter((a) => a.status === status).length;
    const status = Aira.ui.select([["", "Every account"], ...S.statusOptions(["pending", "approved", "paused", "declined"])], query.status || "", { "aria-label": "Status" });
    const search = h("input", { type: "search", placeholder: "Search business, contact, email or tax number", value: query.q || "", "aria-label": "Search business buyers" });
    const apply = () => Aira.router.go(["shop"], { tab: "business", status: status.value, q: search.value.trim() });
    status.addEventListener("change", apply);
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") apply(); });
    const needle = String(query.q || "").toLowerCase();
    const rows = data.accounts.filter((a) => (!query.status || a.status === query.status) && (!needle || `${a.company} ${a.contact_name} ${a.email} ${a.tax_id}`.toLowerCase().includes(needle)));
    const pending = data.accounts.filter((a) => a.status === "pending");
    const settings = S.form([
      { name: "sell_to", label: "Who you sell to", type: "select", options: SELL_TO, hint: "Set once for every app in Business hub > Business details." },
      { name: "trade_approval", label: "New trade accounts", type: "select", options: [["manual", "I approve each business"], ["auto", "Approve automatically on the default terms"]] },
      { name: "trade_default_terms", label: "Default payment terms", type: "select", options: data.terms.map((t) => [t.id, t.label]) },
      { name: "trade_min_order_cents", label: "Business minimum order", type: "money", hint: "Before tax and delivery. Empty for none." },
      { name: "trade_require_po", label: "Business orders need a PO number", type: "checkbox" },
      { name: "trade_intro", label: "Message for businesses on your storefront", type: "textarea", rows: 2, placeholder: "Wholesale for cafes and shops. Net 30 for regular buyers." },
    ], { ...data.settings, sell_to: data.sell_to, trade_min_order_cents: data.settings.trade_min_order_cents || null });
    body.append(
      h("p", { class: "muted cm-lead", text: "Businesses apply on your storefront, or you add them. Once approved they sign in there with their email and trade code, see your trade prices and pay on their terms. Consumers buy as before." }),
      S.kpis([
        { label: "Trade accounts", value: count("approved") },
        { label: "Waiting for approval", value: count("pending"), tone: count("pending") ? "warn" : "" },
        { label: "Owed by businesses", value: S.money(owed.open_cents, currency), tone: owed.open_cents ? "warn" : "" },
        { label: "Overdue", value: S.money(owed.overdue_cents, currency), tone: owed.overdue_cents ? "bad" : "", hint: owed.overdue_orders ? `${owed.overdue_orders} order${owed.overdue_orders === 1 ? "" : "s"}` : "" },
      ]),
      pending.length ? S.section(`Waiting for approval (${pending.length})`, S.table({
        columns: [
          { key: "company", label: "Business" },
          { key: "contact_name", label: "Contact", render: (a) => `${a.contact_name || ""}${a.contact_name ? " · " : ""}${a.email}` },
          { key: "tax_id", label: "Tax number" },
          { key: "created_at", label: "Asked", render: (a) => Aira.fmt.ago(a.created_at) },
        ],
        rows: pending,
        onRow: (a) => openAccount(a.id, rerender, data.terms),
      })) : null,
      S.section("What businesses owe",
        h("div", { class: "btn-row" }, S.button("Download CSV", () => Aira.download("/api/shop/receivables?format=csv", "receivables.csv"), { kind: "secondary sm" })),
        narrow(S.table({
          columns: [
            { key: "number", label: "Order", render: (o) => `#${o.number}` },
            { key: "company", label: "Business" },
            { key: "po_number", label: "PO" },
            { key: "due_on", label: "Due", render: (o) => h("span", {}, S.day(o.due_on), " ", o.overdue ? S.pill(`${o.days_late} days late`, { [`${o.days_late} days late`]: "bad" }) : null) },
            { key: "total_cents", label: "Amount", num: true, render: (o) => S.money(o.total_cents, o.currency) },
            { key: "id", label: "", render: (o) => S.button("Remind", async () => { await call(`/orders/${o.id}/remind`, { method: "POST", body: {} }); Aira.toast("Reminder ready in Business hub > Outbox.", "success"); }, { kind: "ghost sm" }) },
          ],
          rows: owed.orders,
          empty: Aira.ui.empty("Nothing owed", "Unpaid business orders show here with their due dates."),
          onRow: (o) => openOrder(o.id, rerender),
        }), 3)),
      S.section("Trade accounts",
        h("div", { class: "btn-row cm-filters" }, search, status, S.button("Add a business", () => addBusiness(rerender, data.terms)), S.button("Download CSV", () => Aira.download("/api/shop/trade-accounts?format=csv", "trade-accounts.csv"), { kind: "secondary" })),
        narrow(S.table({
          columns: [
            { key: "company", label: "Business", render: (a) => h("span", {}, a.company, a.contact_name ? h("span", { class: "muted small", text: ` · ${a.contact_name}` }) : null) },
            { key: "status", label: "Status", render: (a) => S.pill(a.status, TRADE_TONES) },
            { key: "terms", label: "Terms", render: (a) => a.terms_label },
            { key: "discount_percent", label: "Off", num: true, render: (a) => (a.discount_percent ? `${a.discount_percent}%` : "") },
            { key: "open_cents", label: "Owes", num: true, render: (a) => (a.open_cents ? S.money(a.open_cents, currency) : "") },
            { key: "paid_cents", label: "Paid", num: true, render: (a) => S.money(a.paid_cents, currency) },
            { key: "orders", label: "Orders", num: true },
          ],
          rows,
          empty: query.status || query.q
            ? Aira.ui.empty("No business matches", "Try another name, or clear the filters.", h("a", { class: "btn secondary", href: "#/shop?tab=business", text: "Clear the filters" }))
            : Aira.ui.empty("No business buyers yet", "Share your storefront with businesses: they apply there. Or add one you already sell to.", S.button("Add a business", () => addBusiness(rerender, data.terms), { kind: "secondary" })),
          onRow: (a) => openAccount(a.id, rerender, data.terms),
        }), 3, 4, 6)),
      S.section("How businesses buy", settings.node, h("div", { class: "btn-row" }, S.button("Save", async () => {
        settings.setError("");
        try {
          const values = settings.read();
          const sell = values.sell_to;
          delete values.sell_to;
          await call("/settings", { method: "POST", body: { ...values, trade_min_order_cents: values.trade_min_order_cents || 0 } });
          if (sell !== data.sell_to) await sellTo(sell, null);
          Aira.toast("Saved.", "success");
          rerender();
        } catch (error) { settings.setError(error.message); }
      }))),
    );
    if (take("new", "business")) later(() => addBusiness(rerender, data.terms));
  }

  // ------------------------------------------------------------------ share and earn
  async function referralsTab(body, rerender) {
    const data = await call("/referrals");
    const totals = data.totals;
    const form = S.form([
      { name: "referrals_enabled", label: "Turn on share and earn", type: "checkbox", hint: "Every customer who pays gets their own code to share." },
      { name: "referral_friend_percent", label: "Friends get this percent off their first order", type: "number", min: 0, max: 90, hint: "0 to 90. For example 10." },
      { name: "referral_reward_cents", label: "The customer who shared gets this much off their next order", type: "money", hint: "For each new friend who buys. For example 5.00." },
      { name: "referral_reward_days", label: "Days the reward can be used", type: "number", min: 1, max: 3650 },
    ], { referrals_enabled: data.enabled, referral_friend_percent: data.friend_percent, referral_reward_cents: data.reward_cents, referral_reward_days: data.reward_days });
    body.append(
      h("p", { class: "muted cm-lead", text: "Let your customers earn by sharing your shop. Everyone who pays for an order gets a code of their own (it is on their order page and receipt). A new customer who uses it gets a discount on their first order. When that order is paid, the customer who shared it earns an amount off their next order, sent to them by email." }),
      S.kpis([
        { label: "Customers sharing", value: totals.referrers },
        { label: "Friends brought", value: totals.friends },
        { label: "Sales from friends", value: S.money(totals.sales_cents), tone: totals.sales_cents ? "good" : "" },
        { label: "Rewards earned", value: S.money(totals.rewards_cents), hint: totals.rewards_used ? `${totals.rewards_used} used` : "" },
      ]),
      S.section("How it works for you", form.node, h("div", { class: "btn-row" }, S.button("Save", async () => {
        form.setError("");
        try { await call("/settings", { method: "POST", body: form.read() }); Aira.toast("Saved.", "success"); rerender(); } catch (error) { form.setError(error.message); }
      }))),
      S.section("Customers who share",
        h("div", { class: "btn-row" }, S.button("Download CSV", () => Aira.download("/api/shop/referrals?format=csv", "referrals.csv"), { kind: "secondary" })),
        S.table({
          columns: [
            { key: "name", label: "Customer", render: (r) => r.name || r.email },
            { key: "code", label: "Code" },
            { key: "friends", label: "Friends", num: true },
            { key: "sales_cents", label: "Their sales", num: true, render: (r) => S.money(r.sales_cents) },
            { key: "rewards_cents", label: "Earned", num: true, render: (r) => S.money(r.rewards_cents) },
            { key: "rewards_used", label: "Used", num: true },
          ],
          rows: data.referrers,
          empty: Aira.ui.empty(data.enabled ? "Nobody has shared yet" : "Share and earn is off", data.enabled ? "Codes appear here when customers pay for an order." : "Turn it on above. Customers who pay get a code to share."),
          onRow: (r) => Aira.router.go(["shop"], { tab: "orders", q: r.email }),
        })),
    );
  }

  // ------------------------------------------------------------------ settings
  async function settingsTab(body) {
    const data = await call("/settings");
    const form = S.form([
      { name: "open", label: "Taking orders", type: "checkbox", hint: "Turn off for holidays: customers can browse but not order." },
      { name: "closed_message", label: "Message when closed", type: "textarea", rows: 2 },
      { name: "notice", label: "Store notice", type: "textarea", rows: 2, placeholder: "Free gift wrap this week!" },
      { type: "heading", label: "Pickup" },
      { name: "pickup_enabled", label: "Offer pickup", type: "checkbox" },
      { name: "pickup_instructions", label: "Pickup instructions", type: "textarea", rows: 2, placeholder: "Collect from 12 Main St, Mon-Sat 10-6" },
      { type: "heading", label: "Delivery" },
      { name: "delivery_enabled", label: "Offer delivery", type: "checkbox" },
      { name: "delivery_fee_cents", label: "Delivery fee", type: "money" },
      { name: "free_delivery_over_cents", label: "Free delivery on orders over", type: "money", hint: "0 for no free delivery." },
    ], data.settings);
    body.append(
      S.section("Shop settings", form.node, h("div", { class: "btn-row" }, S.button("Save", async () => { form.setError(""); try { await call("/settings", { method: "POST", body: form.read() }); Aira.toast("Saved.", "success"); } catch (error) { form.setError(error.message); } }))),
      S.section("Business details", h("p", { class: "muted", text: `Your shop name, contact, currency (${data.business.currency}), tax and payment link are set once for every app.` }), h("a", { class: "btn secondary sm", href: "#/hub?tab=business", text: "Edit business details" })),
      S.section("Who you sell to", h("p", { class: "muted", text: `Now: ${(SELL_TO.find(([id]) => id === data.business.sell_to) || SELL_TO[0])[1]}. Selling to businesses adds trade accounts, trade prices and payment terms.` }), h("a", { class: "btn secondary sm", href: "#/shop?tab=business", text: "Business buyers" })),
    );
  }

  // ------------------------------------------------------------------ in the Aira frame: + New, search, folders
  const C = (Aira.commerce = Aira.commerce || {});
  const BOOKING_TONES = { booked: "warn", confirmed: "info", completed: "good", cancelled: "bad", no_show: "bad" };
  // Creating takes an adult (a child's or a teen's household profile gets 403 on every Commerce route) and, hosted,
  // a role that manages the organization's apps (an auditor only reads them).
  C.canCreate = () => {
    const me = Aira.life && Aira.life.me; // the household profile in use (life.js)
    if (me && (me.age_group === "child" || me.age_group === "teen")) return false;
    const cloud = Aira.cloud;
    return !(cloud && cloud.me && typeof cloud.can === "function" && !cloud.can("suite.manage"));
  };
  // Both searches at once, quietly (a section this person cannot open answers nothing), kept a few seconds so the
  // list under the box and the results page share them.
  const recent = new Map();
  C.find = (query, limit = 6) => {
    const key = `${limit}|${query.toLowerCase()}`;
    const hit = recent.get(key);
    if (hit && Date.now() - hit.at < 8000) return hit.promise;
    const words = `?q=${encodeURIComponent(query)}&limit=${limit}`;
    const quiet = (promise) => promise.catch(() => null);
    const promise = Promise.all([quiet(call(`/search${words}`, { silent: true })), quiet(S.api("bookings", `/search${words}`, { silent: true }))])
      .then(([shop, bookings]) => ({ products: [], orders: [], ...(shop || {}), bookings: [], customers: [], services: [], ...(bookings || {}) }));
    recent.set(key, { at: Date.now(), promise });
    if (recent.size > 40) recent.delete(recent.keys().next().value);
    return promise;
  };
  C.forget = () => recent.clear();
  const hits = {
    products: (p) => ({ label: p.name, sub: `Product · ${S.money(p.price_cents)}${p.status === "active" ? "" : ` · ${p.status}`}`, icon: "shop", href: `#/shop?tab=products&product=${p.id}` }),
    orders: (o) => ({ label: `Order #${o.number} · ${o.customer_name}`, sub: `${S.money(o.total_cents, o.currency)} · ${o.payment_status} · ${o.fulfilment_status}`, icon: "applications", href: `#/shop?tab=orders&order=${o.id}` }),
    bookings: (b) => ({ label: `${b.customer_name} · ${b.service_name}`, sub: `Booking ${b.reference} · ${b.local_start} · ${String(b.status).replaceAll("_", " ")}`, icon: "bookings", href: `#/bookings?tab=appointments&booking=${b.id}` }),
    customers: (c) => ({ label: c.name, sub: `Customer${c.email || c.phone ? ` · ${c.email || c.phone}` : ""} · ${c.visits} visit${c.visits === 1 ? "" : "s"}`, icon: "recruiters", href: `#/bookings?tab=customers&customer=${c.id}` }),
    services: (v) => ({ label: v.name, sub: `Service · ${v.minutes} min · ${S.money(v.price_cents)}`, icon: "careers", href: `#/bookings?tab=services&service=${v.id}` }),
  };
  const KINDS = Object.keys(hits);

  Aira.appNew.commerce = () => {
    if (!C.canCreate()) return [];
    const shop = [
      { label: "New order", icon: "applications", href: "#/shop?tab=orders&new=order" },
      { label: "New product", icon: "shop", href: "#/shop?tab=products&new=product" },
      { label: "New discount code", icon: "events", href: "#/shop?tab=discounts&new=discount" },
      // a shop that sells to businesses (the business details read when Commerce last opened)
      sellsToBusinesses(S._business) ? { label: "New business buyer", icon: "careers", href: "#/shop?tab=business&new=business" } : null,
    ].filter(Boolean);
    const bookings = [
      { label: "New booking", icon: "bookings", href: "#/bookings?tab=appointments&new=booking" },
      { label: "New service", icon: "careers", href: "#/bookings?tab=services&new=service" },
    ];
    // One menu for the whole app (the frame draws it once per app): the shop's creates, then the bookings'.
    return [...shop, ...bookings];
  };

  Aira.appSearch.commerce = {
    placeholder: "Search orders, products, bookings and customers",
    // Up to six, taking turns between the kinds so one busy kind does not hide the others.
    async suggest(query) {
      const found = await C.find(query);
      const out = [];
      for (let round = 0; out.length < 6 && KINDS.some((kind) => found[kind].length > round); round += 1) {
        for (const kind of KINDS) if (found[kind][round] && out.length < 6) out.push(hits[kind](found[kind][round]));
      }
      return out;
    },
    run(query) { Aira.router.go(["shop"], { tab: "search", q: query }); },
  };

  // Enter in the search box: everything in Commerce that matches, each opening its record, with the full lists a click away.
  async function results(main, route) {
    const q = String(route.query.q || "").trim();
    const body = S.page(main, { title: q ? `Results for “${q}”` : "Search Commerce", subtitle: "Orders, products, bookings, customers and services." });
    if (Aira.shell) Aira.shell.tabs("shop", TABS, "");
    if (!q) { body.append(Aira.ui.empty("Type what you are looking for", "In the search box at the top: a customer's name, email or phone, an order number like #1001, a product or a booking reference.")); return; }
    const panel = h("div", { class: "suite-body" }, Aira.ui.skeleton(3));
    body.append(panel);
    const found = await C.find(q, 20);
    const go = (href) => () => { location.hash = href; };
    const again = () => { C.forget(); Aira.router.render(); };
    const more = (rows, label, href) => (rows.length >= 20 ? h("a", { class: "btn ghost sm", href, text: label }) : null);
    const blocks = [
      found.orders.length ? S.section(`Orders (${found.orders.length})`, ordersTable(found.orders, again), more(found.orders, "See every matching order", `#/shop?tab=orders&q=${encodeURIComponent(q)}`)) : null,
      found.products.length ? S.section(`Products (${found.products.length})`, narrow(S.table({
        columns: [{ key: "name", label: "Product" }, { key: "category", label: "Category" }, { key: "price_cents", label: "Price", num: true, render: (p) => S.money(p.price_cents) }, { key: "status", label: "Status", render: (p) => S.pill(p.status, { active: "good", draft: "", archived: "" }) }],
        rows: found.products, onRow: (p) => editProduct(p.id, again),
      }), 2), more(found.products, "See every matching product", `#/shop?tab=products&q=${encodeURIComponent(q)}`)) : null,
      found.bookings.length ? S.section(`Bookings (${found.bookings.length})`, narrow(S.table({
        columns: [{ key: "local_start", label: "When" }, { key: "customer_name", label: "Customer" }, { key: "service_name", label: "Service" }, { key: "status", label: "Status", render: (b) => S.pill(b.status, BOOKING_TONES) }, { key: "reference", label: "Ref" }],
        rows: found.bookings, onRow: (b) => go(hits.bookings(b).href)(),
      }), 3, 5), more(found.bookings, "See every matching booking", `#/bookings?tab=appointments&when=all&q=${encodeURIComponent(q)}`)) : null,
      found.customers.length ? S.section(`Booking customers (${found.customers.length})`, narrow(S.table({
        columns: [{ key: "name", label: "Customer" }, { key: "email", label: "Email" }, { key: "phone", label: "Phone" }, { key: "visits", label: "Visits", num: true }],
        rows: found.customers, onRow: (c) => go(hits.customers(c).href)(),
      }), 3), more(found.customers, "See every matching customer", `#/bookings?tab=customers&q=${encodeURIComponent(q)}`)) : null,
      found.services.length ? S.section(`Services (${found.services.length})`, S.table({
        columns: [{ key: "name", label: "Service" }, { key: "minutes", label: "Length", render: (v) => `${v.minutes} min` }, { key: "price_cents", label: "Price", num: true, render: (v) => S.money(v.price_cents) }],
        rows: found.services, onRow: (v) => go(hits.services(v).href)(),
      })) : null,
    ].filter(Boolean);
    if (!panel.isConnected) return;
    panel.replaceChildren(...(blocks.length ? blocks : [Aira.ui.empty(`Nothing in Commerce matches “${q}”`, "Try part of a name, an email, a phone number, an order number like #1001 or a booking reference.", h("a", { class: "btn secondary", href: `#/shop?tab=customers&q=${encodeURIComponent(q)}`, text: "Look in shop customers" }))]));
  }

  // The left menu shows the Shop folders from the start, before the page is first opened.
  if (Aira.shell) Aira.shell.tabs("shop", TABS, null);

  Aira.views.shop = async (main, route) => {
    const query = route.query || {};
    // A once-link (#/shop?tab=orders&new=order): remember what it asks for, take it out of the address so Back and
    // refresh do not open it again, and let the redrawn tab open it.
    if (ONCE.some((key) => query[key])) {
      pending = Object.fromEntries(ONCE.filter((key) => query[key]).map((key) => [key, query[key]]));
      Aira.router.go(["shop"], Object.fromEntries(Object.entries(query).filter(([key]) => !ONCE.includes(key))), { replace: true });
      return;
    }
    await S.business();
    if (query.tab === "search") { pending = {}; await results(main, route); return; }
    const body = S.page(main, { title: "Shop", subtitle: "Sell products online: storefront, orders, stock and customers." });
    const tab = S.tabs(body, route, "shop", TABS);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const render = async () => {
      const holder = h("div", { class: "suite-body" });
      panel.replaceChildren(Aira.ui.skeleton(4));
      try {
        if (tab === "orders") await orders(holder, route, render);
        else if (tab === "products") await productsTab(holder, route, render);
        else if (tab === "discounts") await discountsTab(holder, render);
        else if (tab === "customers") await customersTab(holder, route);
        else if (tab === "business") await businessTab(holder, route, render);
        else if (tab === "referrals") await referralsTab(holder, render);
        else if (tab === "settings") await settingsTab(holder);
        else await overview(holder, render);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
      pending = {}; // a once-link for another tab, or a record that failed to load, does not linger
      C.forget(); // a change shows in the next search
    };
    await render();
  };
})();

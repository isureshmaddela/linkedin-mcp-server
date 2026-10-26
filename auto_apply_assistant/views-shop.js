/* Aira — Shop (#/shop): sell products online.
 *
 * Tabs (?tab=): overview (revenue, orders to fulfil, best sellers, low stock, the storefront link), orders (filters,
 * a drawer per order with payment and fulfilment actions and its customer link, orders entered by hand, CSV),
 * products (stock, options, images, an AI-written description, CSV), discounts (codes), customers (everyone who
 * ordered, CSV) and settings (open or closed, pickup, delivery fee and free-delivery threshold, store notice).
 * Everything runs through /api/shop/* (aira/suite/shop/shop_routes.py). The storefront and order pages customers
 * see are in public-shop.js. DOM is built with h() and text only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const call = (path, opts) => S.api("shop", path, opts);

  const TABS = [
    { id: "overview", label: "Overview" },
    { id: "orders", label: "Orders" },
    { id: "products", label: "Products" },
    { id: "discounts", label: "Discounts" },
    { id: "customers", label: "Customers" },
    { id: "settings", label: "Settings" },
  ];
  const FULFILMENT = ["placed", "packed", "shipped", "delivered", "cancelled"];
  const PAYMENT = ["unpaid", "paid", "refunded"];
  const TONES = { placed: "warn", packed: "info", shipped: "info", delivered: "good", unpaid: "warn", paid: "good", refunded: "bad" };

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
    if (data.customer_says_paid) parts.push(h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: `${data.customer_says_paid} customer${data.customer_says_paid === 1 ? " says they have" : "s say they have"} paid` }), h("span", { class: "muted", text: "Check your payments and mark the orders paid." }), h("a", { class: "btn secondary sm", href: "#/shop?tab=orders", text: "See orders" })));
    if (!data.settings.open) parts.push(h("div", { class: "cloud-banner warn", role: "status" }, h("strong", { text: "Your shop is closed" }), h("span", { class: "muted", text: "Customers can browse but not order." }), h("a", { class: "btn secondary sm", href: "#/shop?tab=settings", text: "Open it" })));
    parts.push(S.kpis([
      { label: "Revenue this month", value: S.money(revenue.this_month_cents, revenue.currency), hint: revenue.change_percent === null ? "" : `${revenue.change_percent >= 0 ? "+" : ""}${revenue.change_percent}% vs last month`, tone: "good" },
      { label: "Orders to fulfil", value: data.to_fulfil, tone: data.to_fulfil ? "warn" : "" },
      { label: "Waiting for payment", value: data.unpaid, tone: data.unpaid ? "warn" : "" },
      { label: "Average order", value: S.money(data.average_order_cents, revenue.currency) },
      { label: "Orders this month", value: data.orders_this_month },
    ]));
    parts.push(storefrontCard(data.storefront, rerender));
    parts.push(S.grid(
      S.section("Best sellers", S.table({ columns: [{ key: "name", label: "Product" }, { key: "quantity", label: "Sold", num: true }, { key: "revenue_cents", label: "Sales", num: true, render: (r) => S.money(r.revenue_cents) }], rows: data.best_sellers, empty: Aira.ui.empty("No sales yet", "Share your storefront to get your first order.") })),
      S.section("Low stock", data.low_stock.length ? S.table({ columns: [{ key: "name", label: "Product" }, { key: "stock", label: "Left", render: (r) => (r.options.length ? r.options.map((o) => `${o.name}: ${o.stock}`).join(", ") : String(r.stock)) }], rows: data.low_stock, onRow: (r) => editProduct(r.id, rerender) }) : Aira.ui.empty("Stock looks fine", "Products that track stock show here when they run low.")),
    ));
    parts.push(S.section("Recent orders", ordersTable(data.recent, rerender)));
    if (!data.products.total) parts.push(Aira.ui.empty("Add your first product", "Name, price and a photo are enough to start selling.", S.button("Add a product", () => editProduct(null, rerender))));
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
  function ordersTable(rows, rerender) {
    return S.table({
      columns: [
        { key: "number", label: "Order", render: (o) => `#${o.number}` },
        { key: "created_at", label: "Placed", render: (o) => Aira.fmt.ago(o.created_at) },
        { key: "customer_name", label: "Customer" },
        { key: "items", label: "Items", num: true, render: itemCount },
        { key: "total_cents", label: "Total", num: true, render: (o) => S.money(o.total_cents, o.currency) },
        { key: "payment_status", label: "Payment", render: (o) => h("span", {}, S.pill(o.payment_status, TONES), o.customer_says_paid_at && o.payment_status === "unpaid" ? h("span", { class: "muted small", text: " says paid" }) : null) },
        { key: "fulfilment_status", label: "Status", render: (o) => S.pill(o.fulfilment_status, TONES) },
      ],
      rows,
      empty: Aira.ui.empty("No orders yet", "Orders from your storefront and the ones you enter yourself show here."),
      onRow: (order) => openOrder(order.id, rerender),
    });
  }

  async function orders(body, route, rerender) {
    const query = route.query || {};
    const search = h("input", { type: "search", placeholder: "Search name, email, phone or #number", value: query.q || "", "aria-label": "Search orders" });
    const fulfilment = Aira.ui.select([["", "Any status"], ...S.statusOptions(FULFILMENT)], query.fulfilment || "", { "aria-label": "Status" });
    const payment = Aira.ui.select([["", "Any payment"], ...S.statusOptions(PAYMENT)], query.payment || "", { "aria-label": "Payment" });
    const apply = () => Aira.router.go(["shop"], { tab: "orders", q: search.value.trim(), fulfilment: fulfilment.value, payment: payment.value });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") apply(); });
    fulfilment.addEventListener("change", apply);
    payment.addEventListener("change", apply);
    const params = new URLSearchParams(Object.entries({ q: query.q || "", fulfilment: query.fulfilment || "", payment: query.payment || "" }).filter(([, v]) => v));
    const data = await call(`/orders${params.toString() ? `?${params}` : ""}`);
    body.append(
      h("div", { class: "btn-row" }, search, fulfilment, payment,
        S.button("New order", () => newOrder(rerender)),
        S.button("Download CSV", () => Aira.download("/api/shop/orders?format=csv", "orders.csv"), { kind: "secondary" })),
      ordersTable(data.orders, rerender),
    );
  }

  async function openOrder(orderId, rerender) {
    const { order } = await call(`/orders/${orderId}`);
    const body = h("div", { class: "suite-body" });
    const refresh = async () => { if (rerender) rerender(); await openOrder(orderId, rerender); };
    const act = (label, path, payload = {}, kind = "secondary sm") => S.button(label, async () => { await call(`/orders/${orderId}/${path}`, { method: "POST", body: payload }); Aira.toast(`Order #${order.number}: ${label.toLowerCase()}.`, "success"); await refresh(); }, { kind });
    const actions = [];
    const live = order.fulfilment_status !== "cancelled";
    if (order.payment_status !== "paid") actions.push(act("Mark paid", "paid", {}, ""));
    if (order.payment_status === "paid") actions.push(act("Mark unpaid", "unpaid", {}, "ghost sm"), S.button("Refund", async () => { if (await Aira.confirm({ title: "Refund this order", message: "Mark the order refunded? Its revenue stops counting. Send the money back through your payment provider yourself.", confirmLabel: "Mark refunded", danger: true })) { await call(`/orders/${orderId}/refund`, { method: "POST", body: {} }); await refresh(); } }, { kind: "ghost sm" }));
    if (live && order.fulfilment_status === "placed") actions.push(act("Packed", "fulfil", { status: "packed" }));
    if (live && ["placed", "packed"].includes(order.fulfilment_status)) actions.push(S.button("Shipped", async () => {
      const result = await S.edit({ title: `Ship order #${order.number}`, fields: [{ name: "tracking_url", label: "Tracking link", type: "url", placeholder: "https://", hint: "Optional. The customer gets it in the shipping email." }], values: { tracking_url: order.tracking_url }, submitLabel: "Mark shipped", onSave: (values) => call(`/orders/${orderId}/fulfil`, { method: "POST", body: { status: "shipped", tracking_url: values.tracking_url } }) });
      if (result) await refresh();
    }, { kind: "secondary sm" }));
    if (live && order.fulfilment_status !== "delivered") actions.push(act(order.delivery_method === "pickup" ? "Picked up" : "Delivered", "fulfil", { status: "delivered" }));
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
    const notes = h("textarea", { rows: 3, placeholder: "Only your team sees these notes", "aria-label": "Internal notes" });
    notes.value = order.internal_notes || "";
    const history = h("ol", { class: "suite-checklist" }, ...(order.history || []).slice().reverse().map((entry) => h("li", { class: "done" }, h("span", { class: "suite-tick", "aria-hidden": "true", text: "✓" }), h("span", {}, h("strong", { text: String(entry.status || "").replaceAll("_", " ") }), h("span", { class: "muted", text: ` ${S.when(entry.at)}${entry.note ? ` · ${entry.note}` : ""}` })))));
    body.append(
      h("div", { class: "btn-row" }, S.pill(order.payment_status, TONES), S.pill(order.fulfilment_status, TONES), order.customer_says_paid_at && order.payment_status === "unpaid" ? h("span", { class: "muted", text: `Customer says paid ${Aira.fmt.ago(order.customer_says_paid_at)}` }) : null),
      h("div", { class: "btn-row" }, ...actions),
      S.section("Items", lines, totals),
      S.section("Customer", customer),
      S.share({ title: "Customer's order page", text: "Shows the order, its status and how to pay.", link: order.link, onCreate: async () => (await call(`/orders/${orderId}/link`, { method: "POST", body: {} })).link, onRevoke: async () => { await call(`/orders/${orderId}/link`, { method: "DELETE" }); } }),
      S.section("Notes", notes, h("div", { class: "btn-row" }, S.button("Save notes", async () => { await call(`/orders/${orderId}`, { method: "PATCH", body: { internal_notes: notes.value } }); Aira.toast("Saved.", "success"); }, { kind: "secondary sm" }))),
      S.section("History", history),
    );
    Aira.drawer({ title: `Order #${order.number}`, subtitle: `${S.money(order.total_cents, order.currency)} · ${S.when(order.created_at)}`, body });
  }

  async function newOrder(rerender) {
    const { products } = await call("/products");
    const choices = products.filter((p) => p.status !== "archived");
    if (!choices.length) { Aira.toast("Add a product first.", "error"); return; }
    const lines = [];
    const list = h("div", { class: "suite-form" });
    const quoteBox = h("div", { class: "muted" });
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
    let timer = null;
    const requote = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const values = form.read();
        try {
          const quote = await call("/quote", { method: "POST", body: { items: payloadItems(), discount_code: values.discount_code, delivery_method: values.delivery_method }, silent: true });
          quoteBox.textContent = `Subtotal ${S.money(quote.subtotal_cents)}${quote.discount_cents ? ` · discount -${S.money(quote.discount_cents)}` : ""}${quote.delivery_cents ? ` · delivery ${S.money(quote.delivery_cents)}` : ""}${quote.tax_cents ? ` · ${quote.tax_label} ${S.money(quote.tax_cents)}` : ""} · Total ${S.money(quote.total_cents)}`;
        } catch (error) { quoteBox.textContent = error.message; }
      }, 250);
    };
    const addLine = () => {
      const product = Aira.ui.select(choices.map((p) => [p.id, `${p.name} · ${S.money(p.price_cents)}${p.status === "draft" ? " (draft)" : ""}`]), choices[0].id, { "aria-label": "Product" });
      const option = h("select", { "aria-label": "Option" });
      const quantity = h("input", { type: "number", min: 1, max: 99, value: 1, "aria-label": "Quantity", style: { width: "80px" } });
      const fillOptions = () => {
        const chosen = choices.find((p) => p.id === product.value);
        option.replaceChildren(...((chosen && chosen.options.length) ? chosen.options.map((o) => h("option", { value: o.name, text: o.name })) : [h("option", { value: "", text: "No options" })]));
        option.disabled = !(chosen && chosen.options.length);
      };
      fillOptions();
      const line = { product, option, quantity };
      const row = h("div", { class: "btn-row" }, product, option, quantity, h("button", { type: "button", class: "btn ghost sm", text: "Remove", onclick: () => { lines.splice(lines.indexOf(line), 1); row.remove(); requote(); } }));
      product.addEventListener("change", () => { fillOptions(); requote(); });
      option.addEventListener("change", requote);
      quantity.addEventListener("input", requote);
      lines.push(line);
      list.append(row);
      requote();
    };
    addLine();
    form.node.addEventListener("change", requote);
    Aira.modal({
      title: "New order",
      wide: true,
      body: h("div", { class: "suite-body" }, h("h3", { class: "suite-h3", text: "Items" }), list, h("div", {}, h("button", { type: "button", class: "btn secondary sm", text: "Add an item", onclick: addLine })), quoteBox, form.node),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Create order", kind: "primary", onClick: async () => {
          form.setError("");
          try {
            const { order } = await call("/orders", { method: "POST", body: { ...form.read(), items: payloadItems() } });
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
      h("div", { class: "btn-row" }, search, status, category, S.button("Add a product", () => editProduct(null, rerender)), S.button("Download CSV", () => Aira.download("/api/shop/products?format=csv", "products.csv"), { kind: "secondary" })),
      S.table({
        columns: [
          { key: "name", label: "Product", render: (p) => h("span", {}, p.images && p.images[0] ? h("img", { src: p.images[0], alt: "", style: { width: "32px", height: "32px", objectFit: "cover", borderRadius: "6px", verticalAlign: "middle", marginRight: "8px" } }) : null, p.name) },
          { key: "category", label: "Category" },
          { key: "price_cents", label: "Price", num: true, render: (p) => S.money(p.price_cents) },
          { key: "stock", label: "Stock", render: (p) => (!p.track_stock ? "Not tracked" : h("span", {}, p.options.some((o) => o.stock !== null) ? p.options.filter((o) => o.stock !== null).map((o) => `${o.name}: ${o.stock}`).join(", ") : String(p.stock), p.low ? h("span", {}, " ", S.pill("low", { low: "bad" })) : null)) },
          { key: "status", label: "Status", render: (p) => S.pill(p.status, { active: "good", draft: "", archived: "" }) },
        ],
        rows: data.products,
        empty: Aira.ui.empty("No products yet", "Add what you sell: a name, a price and a photo are enough.", S.button("Add a product", () => editProduct(null, rerender))),
        onRow: (p) => editProduct(p.id, rerender),
      }),
    );
  }

  async function editProduct(productId, rerender) {
    const product = productId ? (await call(`/products/${productId}`)).product : { status: "active", track_stock: false, stock: 0, low_stock: 3, options: [], images: [] };
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
    ], { ...product, images_text: (product.images || []).join("\n"), options_text: optionsText(product.options) });
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
    Aira.drawer({ title: productId ? product.name : "New product", body: h("div", { class: "suite-body" }, form.node, h("div", { class: "btn-row" }, write), h("div", { class: "btn-row" }, save, remove)) });
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
        empty: Aira.ui.empty("No discount codes", "A code like WELCOME10 brings first-time customers back. Share it with your storefront link."),
        onRow: edit,
      }),
    );
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
      h("div", { class: "btn-row" }, search, S.button("Download CSV", () => Aira.download("/api/shop/customers?format=csv", "customers.csv"), { kind: "secondary" })),
      S.table({
        columns: [
          { key: "name", label: "Name", render: (c) => h("span", {}, c.name, c.repeat ? h("span", {}, " ", S.pill("repeat", { repeat: "good" })) : null) },
          { key: "email", label: "Email" },
          { key: "phone", label: "Phone" },
          { key: "orders", label: "Orders", num: true },
          { key: "spend_cents", label: "Paid", num: true, render: (c) => S.money(c.spend_cents) },
          { key: "last_order_at", label: "Last order", render: (c) => Aira.fmt.ago(c.last_order_at) },
        ],
        rows: data.customers,
        empty: Aira.ui.empty("No customers yet", "Everyone who orders shows here, with what they spent."),
        onRow: (c) => Aira.router.go(["shop"], { tab: "orders", q: c.email || c.phone || c.name }),
      }),
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
    );
  }

  Aira.views.shop = async (main, route) => {
    await S.business();
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
        else if (tab === "settings") await settingsTab(holder);
        else await overview(holder, render);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
  };
})();

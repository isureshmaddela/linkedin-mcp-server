/* Aira — Planner's public pages (aira/suite/planner).
 *
 * shopping_list: the shared shopping list a relative opens without an account. Shows the list by aisle, lets the
 *   visitor tick items and add things (with their name, remembered on this device), and updates by itself every
 *   few seconds when the family ticks something at home (a read-only query, so polling is not a visitor action).
 * for_sale: the household's "things for sale" page. A neighbour sees what is for sale and taps I want this with
 *   their name and a phone number or email (and an offer if they like); the family decides. Side jobs the family
 *   offers (babysitting, dog walking...) are listed under "Jobs we can do" with Ask about this.
 */
"use strict";
(() => {
  const AISLES = { "fruit & veg": "Fruit and vegetables", bakery: "Bakery", dairy: "Milk, cheese and eggs", "meat & fish": "Meat and fish", pantry: "Cupboard", frozen: "Frozen", drinks: "Drinks", household: "Household", pharmacy: "Pharmacy", other: "Other" };
  const remember = (key, value) => { try { localStorage.setItem(`aira.pub.${key}`, value); } catch { /* storage unavailable */ } };
  const recall = (key) => { try { return localStorage.getItem(`aira.pub.${key}`) || ""; } catch { return ""; } };

  window.AiraPublic.register("planner", "shopping_list", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const items = new Map(page.items.map((item) => [item.id, item]));
    let since = page.now;
    const nameInput = h("input", { type: "text", placeholder: "Your name", value: recall("name"), maxlength: "40", autocomplete: "name" });
    nameInput.addEventListener("change", () => remember("name", nameInput.value.trim()));
    const by = () => nameInput.value.trim() || "A guest";
    const list = h("div", { class: "pub-form" });
    const draw = () => {
      const all = [...items.values()].filter((item) => !item.deleted);
      const open = all.filter((item) => !item.checked);
      const ticked = all.filter((item) => item.checked);
      const row = (item) => h("li", { style: { display: "flex", gap: "10px", alignItems: "center", padding: "6px 0" } },
        ctx.button(item.checked ? "✓ Got it" : "Get", async () => {
          const result = await ctx.action("tick", { item_id: item.id, checked: !item.checked, by: by() });
          items.set(result.item.id, result.item);
          draw();
        }, item.checked ? "secondary" : ""),
        h("span", { style: { textDecoration: item.checked ? "line-through" : "none", fontSize: "17px", overflowWrap: "anywhere" }, text: `${item.name}${item.quantity ? ` (${item.quantity})` : ""}` }));
      const groups = Object.keys(AISLES).map((key) => [key, open.filter((item) => item.category === key)]).filter(([, rows]) => rows.length);
      list.replaceChildren(
        ...(groups.length ? groups.map(([key, rows]) => h("section", { class: "pub-card" }, h("h2", { text: AISLES[key] }), h("ul", { style: { listStyle: "none", margin: "0", padding: "0" } }, ...rows.map(row)))) : [h("p", { class: "pub-muted", text: "Nothing left to buy." })]),
        ...(ticked.length ? [h("section", { class: "pub-card" }, h("h2", { text: `In the basket (${ticked.length})` }), h("ul", { style: { listStyle: "none", margin: "0", padding: "0" } }, ...ticked.map(row)))] : []),
      );
    };
    const itemInput = h("input", { type: "text", placeholder: "Something to add", maxlength: "80" });
    const qtyInput = h("input", { type: "text", placeholder: "How many", maxlength: "30" });
    const add = async () => {
      const name = itemInput.value.trim();
      if (!name) { itemInput.focus(); return; }
      const result = await ctx.action("add", { name, quantity: qtyInput.value.trim(), by: by() });
      items.set(result.item.id, result.item);
      itemInput.value = "";
      qtyInput.value = "";
      draw();
    };
    for (const input of [itemInput, qtyInput]) input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); add(); } });
    root.append(
      h("h1", { text: page.list.name }),
      h("p", { class: "pub-muted", text: "A shared shopping list. Tick things as you buy them; the family sees it straight away." }),
      h("section", { class: "pub-card pub-form" }, ctx.field("Your name", nameInput, "So the family knows who ticked it."), ctx.field("Add an item", itemInput), ctx.field("How many", qtyInput), h("div", { class: "pub-btn-row" }, ctx.button("Add", add))),
      list,
    );
    draw();
    const timer = setInterval(async () => {
      if (document.hidden) return;
      try {
        const changes = await ctx.query("changes", { since });
        since = changes.now;
        if (!changes.changed.length) return;
        for (const item of changes.changed) { if (item.deleted) items.delete(item.id); else items.set(item.id, item); }
        draw();
      } catch (error) { if (error.status === 404) clearInterval(timer); }
    }, 5000);
  });

  window.AiraPublic.register("planner", "for_sale", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const money = (cents) => (cents ? ctx.money(cents, page.currency) : "Free");
    const jobPrice = (item) => (item.price_cents ? `${ctx.money(item.price_cents, page.currency)} ${item.price_unit === "hour" ? "an hour" : "a job"}` : "Price agreed with you");
    // A problem is shown inside the form being filled in, where the person is looking (on a phone the top of the
    // page is far away), and the field to fix gets the focus.
    const warn = (form, message, field) => {
      const box = form.querySelector(".pub-ask-error");
      box.textContent = message;
      box.hidden = false;
      if (field) field.focus({ preventScroll: true });
      box.scrollIntoView({ block: "center", behavior: "smooth" });
    };
    const nameInput = () => h("input", { type: "text", maxlength: "60", autocomplete: "name", value: recall("name") });
    const askForm = (item, holder) => {
      const job = item.kind === "service";
      const name = nameInput();
      const contact = h("input", { type: "text", maxlength: "120", autocomplete: "email", inputmode: "email", value: recall("contact"), placeholder: "Phone number or email" });
      const offer = h("input", { type: "text", maxlength: "12", inputmode: "decimal", placeholder: item.price_cents ? (item.price_cents / 100).toFixed(2) : "" });
      const message = h("textarea", { rows: "3", maxlength: "500", placeholder: job ? "What do you need, and when?" : "When could you collect it?" });
      const error = h("p", { class: "pub-notice bad pub-ask-error", role: "alert", hidden: true });
      const form = h("form", { class: "pub-form" },
        error,
        ctx.field("Your name", name),
        ctx.field("Phone number or email", contact, "Only the family sees it, to get in touch with you."),
        job ? null : ctx.field("Your offer (optional)", offer),
        ctx.field(job ? "Message" : "Message (optional)", message),
        h("div", { class: "pub-btn-row" }, ctx.button("Send", async () => {
          error.hidden = true;
          if (!name.value.trim()) { warn(form, "Write your name.", name); return; }
          if (!contact.value.trim()) { warn(form, "Write a phone number or email so they can reach you.", contact); return; }
          try {
            await ctx.action("ask", { item_id: item.id, name: name.value.trim(), contact: contact.value.trim(), offer: job ? "" : offer.value.trim(), message: message.value.trim() });
          } catch (failure) {
            const text = (failure && failure.message) || "That did not go through. Try again.";
            const field = /phone|email/i.test(text) ? contact : /offer/i.test(text) ? offer : /name/i.test(text) ? name : null;
            warn(form, text, field);
            return;
          }
          remember("name", name.value.trim());
          remember("contact", contact.value.trim());
          holder.replaceChildren(h("p", { class: "pub-notice good", role: "status", text: "Sent. The family will get in touch with you." }));
        }), ctx.button("Cancel", () => holder.replaceChildren(askButton(item, holder)), "secondary")));
      form.addEventListener("submit", (event) => event.preventDefault());
      return form;
    };
    const askButton = (item, holder) => ctx.button(item.kind === "service" ? "Ask about this" : "I want this", () => { holder.replaceChildren(askForm(item, holder)); const first = holder.querySelector("input"); if (first) first.focus(); });
    const card = (item) => {
      const holder = h("div");
      if (item.reserved) holder.append(h("p", { class: "pub-muted", text: "Kept for someone for now." }));
      else holder.append(askButton(item, holder));
      const job = item.kind === "service";
      return h("section", { class: "pub-card" },
        h("h2", { style: { overflowWrap: "anywhere" }, text: item.title }),
        job
          ? h("p", {}, h("strong", { text: jobPrice(item) }))
          : h("p", {}, h("strong", { text: money(item.price_cents) }), h("span", { class: "pub-muted", text: ` · ${item.condition}` })),
        item.description ? h("p", { style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere" }, text: item.description }) : null,
        holder);
    };
    const things = page.items.filter((item) => item.kind !== "service");
    const jobs = page.items.filter((item) => item.kind === "service");
    // append() would write a null as the text "null": only real nodes go in.
    root.append(...[
      h("h1", { text: page.title || (jobs.length && !things.length ? "Jobs we can do" : "Things for sale") }),
      page.note ? h("p", { style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere" }, text: page.note }) : null,
      page.items.length ? h("p", { class: "pub-muted", text: `${things.length && jobs.length ? "Tap I want this or Ask about this" : jobs.length ? "Tap Ask about this" : "Tap I want this"} and the family will get in touch to arrange it. You pay them directly.` }) : null,
      ...(page.items.length ? [] : [h("p", { class: "pub-muted", text: "Nothing for sale right now. Check again later." })]),
      jobs.length && things.length ? h("h2", { text: "For sale" }) : null,
      ...things.map(card),
      // The heading, unless the page's own title already says it (a page with only side jobs).
      jobs.length && (things.length || (page.title && page.title !== "Jobs we can do")) ? h("h2", { text: "Jobs we can do" }) : null,
      ...jobs.map(card),
    ].filter(Boolean));
  });
})();

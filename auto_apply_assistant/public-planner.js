/* Aira — Planner: the shared shopping list a relative opens without an account (aira/suite/planner).
 *
 * Shows the list by aisle, lets the visitor tick items and add things (with their name, remembered on this device),
 * and updates by itself every few seconds when the family ticks something at home.
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
    const nameInput = h("input", { type: "text", placeholder: "Your name", value: recall("name"), maxlength: "40" });
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
        h("span", { style: { textDecoration: item.checked ? "line-through" : "none", fontSize: "17px" }, text: `${item.name}${item.quantity ? ` (${item.quantity})` : ""}` }));
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
    itemInput.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); add(); } });
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
        const changes = await ctx.action("refresh", { since });
        since = changes.now;
        if (!changes.changed.length) return;
        for (const item of changes.changed) { if (item.deleted) items.delete(item.id); else items.set(item.id, item); }
        draw();
      } catch (error) { if (error.status === 404) clearInterval(timer); }
    }, 5000);
  });
})();

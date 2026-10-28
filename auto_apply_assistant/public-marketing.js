/* Aira — Marketing: the public pages a business shares (aira/suite/marketing/marketing_routes.py).
 *
 *   go           a tracked link: the visit is counted by the server, the page sends the visitor on at once
 *   form         a lead-capture form (with an optional email opt-in and a hidden field that catches bots)
 *   bio          the link-in-bio page: each link click is counted, then the visitor goes to the link
 *   unsubscribe  one subscriber's unsubscribe page, linked from every newsletter
 *   media_kit    the media kit: audience, rate card and a sponsorship request form
 *
 * DOM is built with ctx.h and text only; only http(s) addresses are ever followed.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  const safe = (url) => typeof url === "string" && /^https?:\/\//i.test(url);

  P.register("marketing", "go", (root, data, ctx) => {
    const { h } = ctx;
    const url = data.page.url;
    if (!safe(url)) { root.append(h("h1", { text: "This link is no longer available" })); return; }
    root.append(h("section", { class: "pub-card" }, h("h1", { text: "Taking you there…" }), h("p", { class: "pub-muted" }, "If nothing happens, ", h("a", { href: url, rel: "noopener", text: "continue to the page" }), ".")));
    window.location.replace(url);
  });

  P.register("marketing", "form", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    root.append(h("h1", { text: page.title }));
    if (page.intro) root.append(h("p", { class: "pub-pre", text: page.intro }));
    if (!page.open) { root.append(h("p", { class: "pub-notice", text: "This form is closed. Contact us directly using the details below." })); return; }
    const LABELS = { name: "Your name", email: "Email", phone: "Phone", company: "Company", message: "Message", custom: page.custom_question || "Your answer" };
    const TYPES = { email: "email", phone: "tel" };
    const inputs = {};
    const fields = [];
    for (const name of ["name", "email", "phone", "company", "message", "custom"]) {
      const spec = page.fields[name];
      if (!spec) continue;
      const input = name === "message" || name === "custom"
        ? h("textarea", { name, required: !!spec.required, maxlength: 2000 })
        : h("input", { name, type: TYPES[name] || "text", required: !!spec.required, maxlength: name === "email" ? 254 : 120, autocomplete: { name: "name", email: "email", phone: "tel", company: "organization" }[name] || "off" });
      inputs[name] = input;
      fields.push(ctx.field(`${LABELS[name]}${spec.required ? " *" : ""}`, input));
    }
    // A field people never see: bots fill it in, and the server then stores nothing.
    const trap = h("input", { name: "website", type: "text", tabindex: "-1", autocomplete: "off", "aria-hidden": "true" });
    const trapWrap = h("div", { style: { position: "absolute", left: "-10000px", width: "1px", height: "1px", overflow: "hidden" } }, h("label", { text: "Leave this empty" }), trap);
    const consent = page.consent_text ? h("input", { type: "checkbox", name: "consent", id: "pubConsent" }) : null;
    // One message at the top of the form, replaced on each try (never a pile of old errors).
    const problem = h("p", { class: "pub-notice bad", role: "alert", hidden: true });
    const form = h("form", { class: "pub-card pub-form", novalidate: true }, problem, ...fields, consent ? h("label", { for: "pubConsent", style: { display: "flex", gap: "8px", alignItems: "flex-start" } }, consent, h("span", { text: page.consent_text })) : null, trapWrap);
    const show = (message, input) => {
      for (const node of Object.values(inputs)) node.removeAttribute("aria-invalid");
      problem.textContent = message || "";
      problem.hidden = !message;
      if (input) { input.setAttribute("aria-invalid", "true"); input.focus(); }
    };
    const send = ctx.button("Send", async () => {
      const payload = { website: trap.value };
      for (const [name, input] of Object.entries(inputs)) payload[name] = input.value.trim();
      if (consent) payload.consent = consent.checked;
      for (const [name, input] of Object.entries(inputs)) if (input.required && !input.value.trim()) { show(`Enter ${LABELS[name].toLowerCase()}.`, input); return; }
      if (inputs.email && payload.email && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(payload.email)) { show("Enter an email address like name@example.com.", inputs.email); return; }
      show("");
      let result;
      try { result = await ctx.action("submit", payload); } catch (error) { show(error.message); return; }
      form.replaceChildren(h("h2", { text: "Sent" }), h("p", { class: "pub-notice good pub-pre", text: result.thank_you }));
    });
    // ctx.button makes type="button" buttons, and a form with no submit button is never sent by Enter (or Go on a
    // phone keyboard). Making Send the submit button lets the browser press it; the page itself never reloads.
    send.type = "submit";
    form.addEventListener("submit", (event) => event.preventDefault());
    form.append(h("div", { class: "pub-btn-row" }, send));
    root.append(form);
  });

  P.register("marketing", "bio", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const head = h("section", { style: { display: "grid", justifyItems: "center", gap: "8px", textAlign: "center", padding: "12px 0" } },
      safe(page.avatar_url) ? h("img", { src: page.avatar_url, alt: "", style: { width: "96px", height: "96px", borderRadius: "50%", objectFit: "cover" } }) : null,
      h("h1", { text: page.title || ctx.business.name || "" }),
      page.bio ? h("p", { class: "pub-muted pub-pre", text: page.bio }) : null);
    root.append(head);
    const list = h("div", { style: { display: "grid", gap: "10px" } });
    if (page.featured && /^\/p\/[A-Za-z0-9_-]+$/.test(page.featured.path)) list.append(h("a", { class: "pub-btn", href: page.featured.path, text: page.featured.label || "Get in touch" }));
    for (const link of page.links) {
      if (!safe(link.url)) continue;
      const anchor = h("a", { class: `pub-btn ${link.highlight ? "" : "ghost"}`.trim(), href: link.url, rel: "noopener", text: link.label });
      anchor.addEventListener("click", async (event) => {
        event.preventDefault();
        try { await ctx.action("click", { link_id: link.id }); } catch { /* a missed count must not block the visitor */ }
        window.location.href = link.url;
      });
      list.append(anchor);
    }
    if (!list.children.length) list.append(h("p", { class: "pub-muted", text: "Nothing here yet." }));
    root.append(list);
  });

  // The media kit: who you are, your audience and prices, and a form a brand uses to ask for a sponsorship.
  P.register("marketing", "media_kit", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    root.append(h("section", { style: { display: "grid", justifyItems: "center", gap: "8px", textAlign: "center", padding: "8px 0" } },
      safe(page.avatar_url) ? h("img", { src: page.avatar_url, alt: "", style: { width: "96px", height: "96px", borderRadius: "50%", objectFit: "cover" } }) : null,
      h("h1", { text: page.title || ctx.business.name || "Work with me" }),
      page.headline ? h("p", { style: { fontSize: "18px", margin: "0" }, text: page.headline }) : null,
      page.niche ? h("span", { class: "pub-badge", text: page.niche }) : null));
    if (page.about) root.append(h("section", { class: "pub-card" }, h("h2", { text: "About" }), h("p", { class: "pub-pre", text: page.about })));
    if (page.audience.length) {
      root.append(h("section", { class: "pub-card" },
        h("h2", { text: `Audience: ${Number(page.reach).toLocaleString()} people` }),
        h("div", { class: "pub-grid" }, ...page.audience.map((item) => h("div", { class: "pub-item" }, h("span", { class: "pub-muted", text: item.label }), h("strong", { style: { fontSize: "22px" }, text: Number(item.count).toLocaleString() }))))));
    }
    if (page.packages.length) {
      root.append(h("section", { class: "pub-card" }, h("h2", { text: "Packages" }),
        h("div", { class: "pub-grid" }, ...page.packages.map((pkg) => h("div", { class: "pub-item" },
          h("strong", { text: pkg.name }),
          h("span", { class: "pub-price", text: pkg.price_cents ? ctx.money(pkg.price_cents) : "Ask for a price" }),
          pkg.details ? h("p", { class: "pub-muted pub-pre", text: pkg.details }) : null,
          pkg.days ? h("span", { class: "pub-muted pub-hint", text: `Delivered in ${pkg.days} day${pkg.days === 1 ? "" : "s"}` } ) : null)))));
    }
    if (!page.open) { root.append(h("p", { class: "pub-notice", text: "Sponsorship requests are closed for now. Contact us directly using the details below." })); return; }
    const inputs = {
      company: h("input", { name: "company", required: true, maxlength: 120, autocomplete: "organization" }),
      name: h("input", { name: "name", required: true, maxlength: 120, autocomplete: "name" }),
      email: h("input", { name: "email", type: "email", required: true, maxlength: 254, autocomplete: "email" }),
      budget: h("input", { name: "budget", inputmode: "decimal", maxlength: 20, placeholder: "0.00" }),
      dates: h("input", { name: "dates", maxlength: 120, placeholder: "For example: early December" }),
      message: h("textarea", { name: "message", maxlength: 2000 }),
    };
    const LABELS = { company: "Brand or company *", name: "Your name *", email: "Email *", budget: `Budget (${ctx.business.currency || "USD"})`, dates: "When", message: "What do you have in mind?" };
    const pick = page.packages.length ? h("select", { name: "package_id" }, h("option", { value: "", text: "Not sure yet" }), ...page.packages.map((pkg) => h("option", { value: pkg.id, text: `${pkg.name}${pkg.price_cents ? ` · ${ctx.money(pkg.price_cents)}` : ""}` }))) : null;
    const trap = h("input", { name: "website", type: "text", tabindex: "-1", autocomplete: "off", "aria-hidden": "true" });
    const trapWrap = h("div", { style: { position: "absolute", left: "-10000px", width: "1px", height: "1px", overflow: "hidden" } }, h("label", { text: "Leave this empty" }), trap);
    const problem = h("p", { class: "pub-notice bad", role: "alert", hidden: true });
    const show = (message, input) => {
      for (const node of Object.values(inputs)) node.removeAttribute("aria-invalid");
      problem.textContent = message || "";
      problem.hidden = !message;
      if (input) { input.setAttribute("aria-invalid", "true"); input.focus(); }
    };
    const form = h("form", { class: "pub-card pub-form", novalidate: true }, h("h2", { text: "Ask about a sponsorship" }), problem,
      ctx.field(LABELS.company, inputs.company), ctx.field(LABELS.name, inputs.name), ctx.field(LABELS.email, inputs.email),
      pick ? ctx.field("Package", pick) : null, ctx.field(LABELS.budget, inputs.budget), ctx.field(LABELS.dates, inputs.dates), ctx.field(LABELS.message, inputs.message), trapWrap);
    const send = ctx.button("Send request", async () => {
      const payload = { website: trap.value, package_id: pick ? pick.value : "" };
      for (const [name, input] of Object.entries(inputs)) payload[name] = input.value.trim();
      for (const name of ["company", "name", "email"]) if (!payload[name]) { show(`Enter ${LABELS[name].replace(" *", "").toLowerCase()}.`, inputs[name]); return; }
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(payload.email)) { show("Enter an email address like name@example.com.", inputs.email); return; }
      if (payload.budget && !/^[0-9][0-9,]*(\.[0-9]{1,2})?$/.test(payload.budget)) { show("Enter the budget as a number, like 500 or 1,200.", inputs.budget); return; }
      show("");
      let result;
      try { result = await ctx.action("request", payload); } catch (error) { show(error.message); return; }
      form.replaceChildren(h("h2", { text: "Request sent" }), h("p", { class: "pub-notice good pub-pre", text: result.thank_you }));
    });
    // ctx.button makes type="button" buttons, and a form with no submit button is never sent by Enter (or Go on a
    // phone keyboard). Making Send the submit button lets the browser press it; the page itself never reloads.
    send.type = "submit";
    form.addEventListener("submit", (event) => event.preventDefault());
    form.append(h("div", { class: "pub-btn-row" }, send));
    root.append(form);
  });

  P.register("marketing", "unsubscribe", (root, data, ctx) => {
    const { h } = ctx;
    const card = h("section", { class: "pub-card" });
    const render = (subscribed) => {
      if (!subscribed) {
        card.replaceChildren(h("h1", { text: "You are unsubscribed" }), h("p", { class: "pub-muted", text: `${data.page.email} will not receive newsletters from ${ctx.business.name || "us"} any more.` }));
        return;
      }
      card.replaceChildren(
        h("h1", { text: "Unsubscribe" }),
        h("p", { text: `Stop newsletters from ${ctx.business.name || "us"} to ${data.page.email}?` }),
        h("div", { class: "pub-btn-row" }, ctx.button("Unsubscribe", async () => { const result = await ctx.action("unsubscribe", {}); render(result.subscribed); })),
      );
    };
    render(data.page.subscribed);
    root.append(card);
  });
})();

/* Aira — Marketing: the public pages a business shares (aira/suite/marketing/marketing_routes.py).
 *
 *   go           a tracked link: the visit is counted by the server, the page sends the visitor on at once
 *   form         a lead-capture form (with an optional email opt-in and a hidden field that catches bots)
 *   bio          the link-in-bio page: each link click is counted, then the visitor goes to the link
 *   unsubscribe  one subscriber's unsubscribe page, linked from every newsletter
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
    const form = h("form", { class: "pub-card pub-form", novalidate: true }, ...fields, consent ? h("label", { for: "pubConsent", style: { display: "flex", gap: "8px", alignItems: "flex-start" } }, consent, h("span", { text: page.consent_text })) : null, trapWrap);
    const send = ctx.button("Send", async () => {
      const payload = { website: trap.value };
      for (const [name, input] of Object.entries(inputs)) payload[name] = input.value.trim();
      if (consent) payload.consent = consent.checked;
      for (const [name, input] of Object.entries(inputs)) if (input.required && !input.value.trim()) { input.focus(); throw new Error(`Enter ${LABELS[name].toLowerCase()}.`); }
      const result = await ctx.action("submit", payload);
      form.replaceChildren(h("h2", { text: "Sent" }), h("p", { class: "pub-notice good pub-pre", text: result.thank_you }));
    });
    form.addEventListener("submit", (event) => { event.preventDefault(); send.click(); });
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

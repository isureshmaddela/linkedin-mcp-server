/* Aira — Developers (#/developers): connect other apps to Aira, and the consent screen (#/oauth).
 *
 * Tabs (?tab=): start (how to connect, in three steps, with copy-paste examples), apps (OAuth apps the organization
 * registered: client id, secret shown once, redirect addresses, scopes), tokens (personal access tokens for scripts
 * and no-code tools), webhooks (signed event posts, deliveries), connected (apps you allowed) and reference (every
 * route a token can call, with its scope; the OpenAPI file). Runs on /api/developers/* and /oauth/* (the hosted
 * build: aira_cloud/routes/developers.py). The single-user build has no developer platform and says so.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const TABS = [
    { id: "start", label: "Get started", icon: "aa-book" },
    { id: "Apps", label: "Apps", icon: "dashboard" },
    { id: "tokens", label: "Access tokens", icon: "aa-key" },
    { id: "webhooks", label: "Webhooks", icon: "aa-pulse" },
    { id: "connected", label: "Connected apps", icon: "settings" },
    { id: "reference", label: "API reference", icon: "documents" },
  ];
  // "+ New" links here with ?new=<kind> (views-airaapi.js): the tab that owns it opens its form once, then the address
  // drops ?new= so Back or a reload does not open it again.
  const NEW_BUTTONS = { token: "devNewToken", app: "devNewApp", webhook: "devNewWebhook" };
  function openNew(route, holder) {
    const kind = route.query.new;
    if (!kind) return;
    const query = { ...route.query };
    delete query.new;
    history.replaceState(null, "", `#/developers?${new URLSearchParams(query)}`);
    if (Aira.router.current) Aira.router.current.query = query;
    delete route.query.new;
    const button = NEW_BUTTONS[kind] && holder.querySelector(`#${NEW_BUTTONS[kind]}`);
    if (button) button.click();
  }
  const withId = (node, id) => { node.id = id; return node; };
  const code = (text) => h("pre", { class: "suite-pre", translate: "no" }, h("code", { text }));
  const copyable = (label, value) => h("div", { class: "dev-secret" }, h("strong", { text: label }), h("code", { translate: "no", text: value }), S.button("Copy", () => S.copy(value), { kind: "secondary sm" }));
  const once = (title, pairs) => Aira.modal({ title, body: h("div", {}, h("p", { class: "cloud-banner warning", text: "Copy this now. For your safety it will not be shown again." }), ...pairs.map(([label, value]) => copyable(label, value))), actions: [{ label: "I have copied it", kind: "primary" }] });

  function scopePicker(scopes, chosen = [], { personalAllowed = true } = {}) {
    const boxes = new Map();
    const groups = {};
    for (const scope of scopes) {
      if (!personalAllowed && scope.personal) continue;
      const app = Aira.APPS.find((entry) => entry.id === scope.app);
      const key = app ? app.name : "General";
      (groups[key] = groups[key] || []).push(scope);
    }
    const node = h("div", { class: "dev-scopes" }, ...Object.entries(groups).map(([name, items]) => h("fieldset", {}, h("legend", { text: name }), ...items.map((scope) => {
      const box = h("input", { type: "checkbox", checked: chosen.includes(scope.scope) });
      boxes.set(scope.scope, box);
      return h("label", { class: "suite-check" }, box, h("span", {}, h("span", { text: scope.label }), h("code", { class: "muted small", translate: "no", text: ` ${scope.scope}` })));
    }))));
    return { node, read: () => [...boxes].filter(([, box]) => box.checked).map(([scope]) => scope) };
  }

  async function start(body, data) {
    const base = data.base_url;
    body.append(
      S.section("Connect another app to Monosage", h("p", { class: "muted", text: "Other apps can read and change your Monosage data safely, with your permission. Pick the way that fits." }),
        h("ol", { class: "dev-steps" },
          h("li", {}, h("strong", { text: "For your own script or a no-code tool: " }), h("span", { text: "make an access token under Access tokens and send it as a Bearer token." })),
          h("li", {}, h("strong", { text: "For an app other people sign in to: " }), h("span", { text: "register it under Apps and use OAuth 2.0 with PKCE. People see exactly what it asks for and can disconnect it any time." })),
          h("li", {}, h("strong", { text: "To hear about changes as they happen: " }), h("span", { text: "add a webhook. Monosage posts signed events to your address." })))),
      S.section("Call the API with a token",
        code(`curl -H "Authorization: Bearer aira_pat_..." ${base}/api/studio/overview`),
        code(`const res = await fetch("${base}/api/shop/orders", {\n  headers: { Authorization: "Bearer " + process.env.AIRA_TOKEN },\n});\nconsole.log(await res.json());`),
        code(`import os, requests\nr = requests.get("${base}/api/bookings/overview",\n                 headers={"Authorization": "Bearer " + os.environ["AIRA_TOKEN"]})\nprint(r.json())`)),
      S.section("Sign people in with OAuth 2.0",
        h("p", { class: "muted", text: "Standard authorization code flow with PKCE (S256). Any OAuth 2.0 library works: point it at the server metadata." }),
        code(`${base}/.well-known/oauth-authorization-server`),
        code(`1. Send the person to:\n${base}/oauth/authorize?response_type=code&client_id=CLIENT_ID&redirect_uri=REDIRECT&scope=shop:read%20profile&state=STATE&code_challenge=CHALLENGE&code_challenge_method=S256\n\n2. Exchange the code:\ncurl -X POST ${base}/oauth/token -d grant_type=authorization_code -d code=CODE -d redirect_uri=REDIRECT -d code_verifier=VERIFIER -u CLIENT_ID:CLIENT_SECRET\n\n3. Refresh (the refresh token changes every time):\ncurl -X POST ${base}/oauth/token -d grant_type=refresh_token -d refresh_token=REFRESH -u CLIENT_ID:CLIENT_SECRET`)),
      S.section("Check a webhook's signature",
        code(`// Node.js: header "Monosage-Signature: t=TIMESTAMP,v1=HEX"\nconst [t, v1] = header.split(",").map((p) => p.split("=")[1]);\nconst expected = crypto.createHmac("sha256", SECRET).update(t + "." + rawBody).digest("hex");\nconst ok = crypto.timingSafeEqual(Buffer.from(v1), Buffer.from(expected)) && Date.now() / 1000 - t < 300;`)),
    );
  }

  async function apps(body, data, rerender) {
    const listed = data.can_read ? (await Aira.api("/api/developers/clients")).clients : [];
    const register = () => {
      const picker = scopePicker(data.scopes, ["profile"]);
      const fields = S.form([
        { name: "name", label: "App name", required: true },
        { name: "homepage", label: "Homepage", type: "url", placeholder: "https://" },
        { name: "redirect_uris", label: "Redirect addresses (one per line)", type: "textarea", rows: 3, hint: "Exact https addresses, or http://127.0.0.1:PORT/... for an app on a computer." },
        { name: "kind", label: "Kind of app", type: "select", options: [["confidential", "Runs on a server (has a secret)"], ["public", "Runs in a browser or on a phone (no secret, PKCE)"]] },
        { name: "machine", label: "Also lets the app act for the organization itself (client credentials)", type: "checkbox" },
      ]);
      Aira.modal({ title: "Register an app", wide: true, body: h("div", {}, fields.node, h("h3", { class: "suite-h3", text: "What it may ask for" }), picker.node), actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Register", kind: "primary", onClick: async () => {
          const v = fields.read();
          const grants = ["authorization_code", "refresh_token", ...(v.machine ? ["client_credentials"] : [])];
          try {
            const made = (await Aira.api("/api/developers/clients", { method: "POST", body: { name: v.name, homepage: v.homepage, redirect_uris: v.redirect_uris.split(/\s+/).filter(Boolean), confidential: v.kind !== "public", grants, scopes: picker.read() } })).client;
            once("Your app is registered", [["Client ID", made.client_id], ...(made.client_secret ? [["Client secret", made.client_secret]] : [])]);
            rerender();
            return true;
          } catch (error) { fields.setError(error.message); return false; }
        } },
      ] });
    };
    body.append(S.section("Apps", h("p", { class: "muted", text: "Apps that can ask people in your organization for access with OAuth 2.0." }),
      data.can_manage ? h("div", { class: "btn-row" }, withId(S.button("Register an app", register), "devNewApp")) : null,
      S.table({ columns: [
        { key: "name", label: "App" },
        { key: "client_id", label: "Client ID", render: (c) => h("code", { translate: "no", text: c.client_id }) },
        { key: "kind", label: "Kind", render: (c) => (c.confidential ? "Server" : "Browser or phone") },
        { key: "scopes", label: "Scopes", render: (c) => h("span", { translate: "no", class: "small", text: c.scopes.join(" ") }) },
        { key: "actions", label: "", render: (c) => data.can_manage ? h("div", { class: "btn-row" },
          c.confidential ? S.button("New secret", async () => { const r = await Aira.api(`/api/developers/clients/${c.client_id}/secret`, { method: "POST" }); once("New client secret", [["Client secret", r.client_secret]]); }, { kind: "ghost sm" }) : null,
          S.button("Delete", async () => { if (await S.confirmDelete(c.name)) { await Aira.api(`/api/developers/clients/${c.client_id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })) : "" },
      ], rows: listed, empty: "No apps yet" })));
  }

  async function tokens(body, data, rerender) {
    const create = () => {
      const picker = scopePicker(data.scopes);
      const fields = S.form([{ name: "name", label: "What uses it", required: true, placeholder: "My Google Sheet, Zapier, a script" }, { name: "days", label: "Expires after", type: "select", options: [["7", "7 days"], ["30", "30 days"], ["90", "90 days"], ["365", "1 year"]], value: "90" }]);
      Aira.modal({ title: "New access token", wide: true, body: h("div", {}, fields.node, h("h3", { class: "suite-h3", text: "What it may do" }), picker.node), actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Make token", kind: "primary", onClick: async () => {
          const v = fields.read();
          try { const made = (await Aira.api("/api/developers/tokens", { method: "POST", body: { name: v.name, days: Number(v.days), scopes: picker.read() } })).token; once("Your access token", [["Token", made.token]]); rerender(); return true; } catch (error) { fields.setError(error.message); return false; }
        } },
      ] });
    };
    body.append(S.section("Personal access tokens", h("p", { class: "muted", text: "A token acts as you, with only the access you give it. Keep it secret like a password." }),
      h("div", { class: "btn-row" }, withId(S.button("New token", create), "devNewToken")),
      S.table({ columns: [
        { key: "name", label: "Name" },
        { key: "scopes", label: "Scopes", render: (t) => h("span", { translate: "no", class: "small", text: t.scopes.join(" ") }) },
        { key: "expires_at", label: "Expires", render: (t) => Aira.fmt.date(t.expires_at) },
        { key: "last_used_at", label: "Last used", render: (t) => (t.last_used_at ? Aira.fmt.ago(t.last_used_at) : "Never") },
        { key: "x", label: "", render: (t) => S.button("Revoke", async () => { await Aira.api(`/api/developers/tokens/${t.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" }) },
      ], rows: data.personal_tokens, empty: "No tokens yet" })));
  }

  async function webhooks(body, data, rerender) {
    if (!data.can_read) { body.append(Aira.ui.empty("Ask an admin", "Webhooks are managed by your organization's admins.")); return; }
    const listed = await Aira.api("/api/developers/webhooks");
    const add = () => {
      const fields = S.form([{ name: "url", label: "Address", type: "url", required: true, placeholder: "https://example.com/aira-events" }, { name: "description", label: "What it is for" }]);
      const events = h("div", { class: "dev-scopes" }, ...listed.events.map((event) => h("label", { class: "suite-check" }, h("input", { type: "checkbox", value: event.event }), h("span", {}, h("span", { text: event.label }), h("code", { class: "muted small", translate: "no", text: ` ${event.event}` })))));
      Aira.modal({ title: "Add a webhook", wide: true, body: h("div", {}, fields.node, h("h3", { class: "suite-h3", text: "Events" }), events), actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Add", kind: "primary", onClick: async () => {
          const v = fields.read();
          const chosen = [...events.querySelectorAll("input:checked")].map((box) => box.value);
          try { const made = (await Aira.api("/api/developers/webhooks", { method: "POST", body: { ...v, events: chosen } })).endpoint; once("Signing secret", [["Secret", made.secret]]); rerender(); return true; } catch (error) { fields.setError(error.message); return false; }
        } },
      ] });
    };
    const deliveries = async (endpoint) => {
      const rows = (await Aira.api(`/api/developers/webhooks/${endpoint.endpoint_id}/deliveries`)).deliveries;
      Aira.modal({ title: "Recent deliveries", wide: true, body: S.table({ columns: [{ key: "created_at", label: "When", render: (d) => Aira.fmt.ago(d.created_at) }, { key: "event", label: "Event", render: (d) => h("code", { translate: "no", text: d.event }) }, { key: "status", label: "Status", render: (d) => S.pill(d.status, { delivered: "good", failed: "bad", pending: "warn" }) }, { key: "attempts", label: "Tries", num: true }, { key: "last_error", label: "Error" }], rows, empty: "Nothing sent yet" }), actions: [{ label: "Close", kind: "secondary" }] });
    };
    body.append(S.section("Webhooks", h("p", { class: "muted", text: "Monosage posts each event to your address, signed with your secret, and tries again if it fails." }),
      data.can_manage ? h("div", { class: "btn-row" }, withId(S.button("Add a webhook", add), "devNewWebhook")) : null,
      S.table({ columns: [
        { key: "url", label: "Address", render: (e) => h("code", { translate: "no", text: e.url }) },
        { key: "events", label: "Events", render: (e) => h("span", { translate: "no", class: "small", text: e.events.join(" ") }) },
        { key: "active", label: "Status", render: (e) => S.pill(e.active ? "active" : "off") },
        { key: "x", label: "", render: (e) => h("div", { class: "btn-row" },
          S.button("Send a test", async () => { const r = await Aira.api(`/api/developers/webhooks/${e.endpoint_id}/test`, { method: "POST" }); Aira.toast(r.status === "delivered" ? "Delivered." : `Not delivered: ${r.error || r.status}`, r.status === "delivered" ? "success" : "error"); }, { kind: "ghost sm" }),
          S.button("Deliveries", () => deliveries(e), { kind: "ghost sm" }),
          data.can_manage ? S.button("Delete", async () => { if (await S.confirmDelete("this webhook")) { await Aira.api(`/api/developers/webhooks/${e.endpoint_id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }) : null) },
      ], rows: listed.endpoints, empty: "No webhooks yet" })));
  }

  async function connected(body, data, rerender) {
    body.append(S.section("Apps you allowed", h("p", { class: "muted", text: "These apps can use your Monosage data with the access you gave them. Disconnect one to stop it at once." }),
      S.table({ columns: [{ key: "name", label: "App" }, { key: "scopes", label: "Access", render: (c) => h("span", { translate: "no", class: "small", text: c.scopes.join(" ") }) }, { key: "since", label: "Since", render: (c) => Aira.fmt.date(c.since) },
        { key: "x", label: "", render: (c) => S.button("Disconnect", async () => { await Aira.api(`/api/developers/connected/${c.client_id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" }) }], rows: data.connected, empty: "You have not connected any apps" })));
  }

  async function reference(body) {
    const spec = await Aira.api("/api/developers/openapi.json");
    const rows = Object.entries(spec.paths).flatMap(([path, ops]) => Object.entries(ops).map(([method, op]) => ({ method: method.toUpperCase(), path, scope: op["x-scope"], summary: op.summary })));
    body.append(S.section("API reference", h("p", { class: "muted", text: "Every route a token can call, and the scope it needs. The same list as an OpenAPI 3.1 file works with Postman, code generators and API tools." }),
      h("div", { class: "btn-row" }, S.button("Download OpenAPI file", () => Aira.download("/api/developers/openapi.json", "aira-openapi.json"), { kind: "secondary" })),
      S.table({ columns: [{ key: "method", label: "Method", render: (r) => h("code", { translate: "no", text: r.method }) }, { key: "path", label: "Path", render: (r) => h("code", { translate: "no", text: r.path }) }, { key: "scope", label: "Scope", render: (r) => h("code", { translate: "no", text: r.scope }) }, { key: "summary", label: "What it does" }], rows, empty: "No routes" })));
  }

  Aira.views.developers = async (main, route) => {
    const body = S.page(main, { title: "Connected apps", subtitle: "Let other apps use your Monosage data safely: OAuth 2.0, access tokens and webhooks." });
    const hostedOnly = "Connecting other apps with OAuth, tokens and webhooks is part of the hosted Monosage, where other systems can reach it safely. On this computer, your scripts and tools call Monosage through the API console's /v1 endpoint with an API key.";
    // What a script can use here instead: the API console and its keys.
    const instead = () => h("div", { class: "btn-row" },
      h("a", { class: "btn", href: "#/api?tab=keys&new=key", text: "Create an API key" }),
      h("a", { class: "btn secondary", href: "#/api?tab=docs", text: "Read the API docs" }));
    // The single-user build has no developer platform: say so without asking a server that does not have it.
    if (!Aira.cloud) { body.append(Aira.ui.empty("Available in the hosted Monosage", hostedOnly, instead())); return; }
    let data;
    try { data = await Aira.api("/api/developers/overview", { silent: true }); } catch (error) {
      body.append(Aira.ui.empty("Available in the hosted Monosage", error.status === 404 ? hostedOnly : error.message, error.status === 404 ? instead() : null));
      return;
    }
    const tab = S.tabs(body, route, "developers", TABS);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const render = async () => {
      const holder = h("div", { class: "suite-body" });
      panel.replaceChildren(Aira.ui.skeleton(3));
      try {
        data = await Aira.api("/api/developers/overview", { silent: true });
        if (tab === "Apps") await apps(holder, data, render);
        else if (tab === "tokens") await tokens(holder, data, render);
        else if (tab === "webhooks") await webhooks(holder, data, render);
        else if (tab === "connected") await connected(holder, data, render);
        else if (tab === "reference") await reference(holder);
        else await start(holder, data);
        panel.replaceChildren(holder);
        openNew(route, holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
  };

  // The consent screen an app sends people to (/oauth/authorize -> #/oauth?...).
  Aira.views.oauth = async (main, route) => {
    const request = { ...route.query };
    const body = S.page(main, { title: "Allow access", subtitle: "" });
    let info;
    try { info = await Aira.api(`/api/oauth/authorize?${new URLSearchParams(request)}`); } catch (error) {
      body.append(h("div", { class: "cloud-banner danger", role: "alert" }, h("strong", { text: "This request cannot be used" }), h("span", { text: error.message })));
      return;
    }
    const decide = async (allow) => {
      const result = await Aira.api("/api/oauth/authorize", { method: "POST", body: { request, allow } });
      location.assign(result.redirect);
    };
    body.append(h("section", { class: "card dev-consent" },
      h("h2", { text: `${info.client.name} wants to use your Monosage account` }),
      h("p", { class: "muted", text: `Made by ${info.client.organization}${info.client.homepage ? ` · ${info.client.homepage}` : ""}` }),
      h("p", { text: "It will be able to:" }),
      h("ul", { class: "dev-consent-list" }, ...info.scopes.map((scope) => h("li", {}, h("strong", { text: scope.label }), h("code", { class: "muted small", translate: "no", text: ` ${scope.scope}` })))),
      h("p", { class: "muted small", text: `After you choose, you go back to ${new URL(info.redirect_uri).host}. You can disconnect it any time under Developers, Connected apps.` }),
      h("div", { class: "btn-row" }, S.button("Allow", () => decide(true)), S.button("Deny", () => decide(false), { kind: "secondary" }))));
  };
})();

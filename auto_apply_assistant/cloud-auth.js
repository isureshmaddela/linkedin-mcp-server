/* Aira hosted build — sign-in, sign-up, invitations, verification, password reset, MFA, sessions and the Security section.
 *
 * Everything here renders into #authScreen (the screens people see before they have a session, and the gates that
 * block the app until an email is verified or a second factor is given) or into the app (the Security section).
 * The pure logic (password hint, route parsing) is exported for node tests; in the browser it is Aira.cloud.auth.logic.
 */
"use strict";

(function () {
  // -------------------------------------------------------------------------
  // Pure logic
  // -------------------------------------------------------------------------
  const ANONYMOUS = [
    { re: /^#\/invite\/([A-Za-z0-9_-]{20,80})$/, screen: "invite" },
    { re: /^#\/verify\/([A-Za-z0-9_-]{20,80})$/, screen: "verify" },
    { re: /^#\/reset\/([A-Za-z0-9_-]{20,80})$/, screen: "reset" },
    { re: /^#\/forgot$/, screen: "forgot" },
    { re: /^#\/signup$/, screen: "signup" },
    { re: /^#\/login$/, screen: "login" },
  ];

  function parseAnonymous(hash) {
    for (const route of ANONYMOUS) {
      const match = route.re.exec(hash || "");
      if (match) return { screen: route.screen, token: match[1] || "" };
    }
    return null;
  }

  // A hint, not a rule: the server decides. It mirrors the server's rules so people are not surprised.
  function strength(password, email) {
    const value = String(password || "");
    const local = String(email || "").split("@")[0].toLowerCase();
    if (!value) return { level: 0, label: "", ok: false };
    if (value.length < 12) return { level: 1, label: `${value.length} of 12 characters`, ok: false };
    if (new Set(value).size < 5) return { level: 1, label: "Use a wider mix of characters", ok: false };
    if (local.length >= 4 && value.toLowerCase().includes(local)) return { level: 1, label: "Do not include your email name", ok: false };
    const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(value)).length;
    const words = value.trim().split(/\s+/).length;
    if (value.length >= 20 || words >= 4 || (value.length >= 16 && classes >= 3)) return { level: 3, label: "Strong", ok: true };
    return { level: 2, label: "Acceptable. Longer is stronger", ok: true };
  }

  function groups(secret) {
    return String(secret || "").replace(/\s+/g, "").replace(/(.{4})/g, "$1 ").trim();
  }

  function cleanCode(value) {
    return String(value || "").trim().replace(/\s+/g, "");
  }

  const logic = { parseAnonymous, strength, groups, cleanCode };
  if (typeof window === "undefined") { module.exports = logic; return; }

  // -------------------------------------------------------------------------
  // Browser
  // -------------------------------------------------------------------------
  const { api, ui, modal } = Aira;
  const cloud = Aira.cloud;
  const auth = (cloud.auth = { logic });

  const FALLBACK_NOTICE = "LinkedIn's User Agreement forbids automated access. Connecting LinkedIn lets Aira search, read and (if you enable it) apply and reply through your own session, which can lead LinkedIn to restrict your account.";

  // While a gate (verify email, second factor) is up, the app behind it must not request anything: it routes to this blank view.
  let gated = false;
  Aira.views.gate = (stage) => stage.replaceChildren();
  const reload = () => { if (parseAnonymous(location.hash) || location.hash === "#/gate") location.hash = ""; location.reload(); };

  function field(label, attrs, hint) {
    const input = h("input", { required: true, ...attrs });
    return { input, node: ui.field(label, input, hint) };
  }

  function strengthMeter(input, getEmail) {
    const bar = h("span", { class: "ca-meter", "aria-hidden": "true" }, h("i"), h("i"), h("i"));
    const text = h("span", { class: "ca-meter-text", role: "status", "aria-live": "polite" });
    const update = () => {
      const result = strength(input.value, getEmail ? getEmail() : "");
      bar.dataset.level = String(result.level);
      text.textContent = result.label;
      text.className = `ca-meter-text ${result.ok ? "ok" : ""}`;
    };
    input.addEventListener("input", update);
    return { node: h("div", { class: "ca-strength" }, bar, text), update };
  }

  function brand() {
    return h("div", { class: "brand", "data-org-brand": "" }, h("span", { class: "brand-mark", text: "A" }), h("span", {}, "AIRA", h("small", { text: "Career Copilot" })));
  }

  // Team and Enterprise organizations show their own name and logo on the sign-in page (the plan decides; others get no change).
  const brands = new Map();
  async function applyOrgBrand(panel, slug) {
    if (!slug || !/^[a-z0-9-]{1,60}$/.test(slug)) return;
    if (!brands.has(slug)) brands.set(slug, Aira.api(`/api/orgs/${encodeURIComponent(slug)}/branding`, { silent: true }).catch(() => ({ branded: false })));
    const info = await brands.get(slug);
    const slot = panel.querySelector("[data-org-brand]");
    if (!info.branded || !slot || !slot.isConnected) return;
    const mark = info.logo_url ? h("img", { class: "ca-org-logo", src: info.logo_url, alt: "", width: 40, height: 40, referrerpolicy: "no-referrer" }) : h("span", { class: "brand-mark", text: (info.name || "?")[0].toUpperCase() });
    slot.replaceChildren(mark, h("span", {}, info.name, h("small", { text: "Career support, powered by Aira" })));
  }

  // One panel, replaced on every screen change.
  function show(title, intro, ...content) {
    const screen = $("authScreen");
    $("boot").hidden = true;
    $("app").hidden = true;
    screen.hidden = false;
    const panel = h("section", { class: "auth-panel cloud-auth", "aria-labelledby": "caTitle" }, brand(), h("h1", { id: "caTitle", style: { marginTop: "18px" }, text: title }), intro ? h("p", { class: "muted", text: intro }) : null, ...content);
    screen.replaceChildren(panel);
    applyOrgBrand(panel, new URLSearchParams(location.hash.split("?")[1] || "").get("org") || Aira.recall("lastOrg", ""));
    const first = panel.querySelector("input:not([type=hidden]), select, button.btn");
    if (first) first.focus();
    document.title = `${title} · Aira`;
    return panel;
  }

  function noticeNode() { return h("p", { class: "ca-notice", role: "status", "aria-live": "polite" }); }
  const say = (node, message, bad = true) => { node.textContent = message; node.classList.toggle("bad", bad && !!message); };
  const link = (text, hash) => h("a", { href: hash, text, onclick: () => { if (location.hash === hash) auth.render(); } });
  const swap = (...parts) => h("p", { class: "switch" }, ...parts);

  async function signOut() {
    try { await api("/api/auth/logout", { method: "POST", body: {}, silent: true }); } catch { /* the cookie may already be gone */ }
    location.hash = "";
    location.reload();
  }

  // -------------------------------------------------------------------------
  // Screens
  // -------------------------------------------------------------------------
  function renderLogin(prefill = {}) {
    const email = field("Email", { type: "email", autocomplete: "username", maxlength: 254, value: prefill.email || "" });
    const password = field("Password", { type: "password", autocomplete: "current-password", maxlength: 1024 });
    const orgSlot = h("div");
    const ssoSlot = h("div", { class: "ca-sso" });
    const notice = noticeNode();
    const submit = h("button", { class: "btn", type: "submit", style: { width: "100%", marginTop: "8px" }, text: "Sign in" });
    let discoverTimer = 0;
    const discover = () => {
      clearTimeout(discoverTimer);
      discoverTimer = setTimeout(async () => {
        const value = email.input.value.trim();
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) { ssoSlot.replaceChildren(); return; }
        try {
          const found = await api("/api/sso/discover", { method: "POST", body: { email: value }, silent: true });
          const orgs = found.organizations || found.orgs || (found.slug ? [found] : []);
          ssoSlot.replaceChildren(...orgs.map((org) => h("a", { class: "btn secondary", href: `/api/sso/${encodeURIComponent(org.slug)}/start`, style: { width: "100%", marginTop: "8px" }, text: orgs.length > 1 ? `Continue with single sign-on (${org.name || org.slug})` : "Continue with single sign-on" })));
        } catch { ssoSlot.replaceChildren(); } // not available, or no single sign-on for this address
      }, 350);
    };
    email.input.addEventListener("input", discover);
    const form = h("form", { novalidate: true }, email.node, password.node, orgSlot, notice, submit, ssoSlot);
    let orgChoice = null;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      say(notice, "Signing in…", false);
      await Aira.withBusy(submit, async () => {
        try {
          const data = await api("/api/auth/login", { method: "POST", body: { email: email.input.value, password: password.input.value, org: orgChoice ? orgChoice.value : "" }, silent: true });
          password.input.value = "";
          if (data.mfa_required) renderMfa({ gate: false }); else reload();
        } catch (error) {
          if (error.status === 409 && error.code === "org_required" && error.data && error.data.orgs) {
            orgChoice = h("select", { id: "caOrg" }, ...error.data.orgs.map((org) => h("option", { value: org.slug, text: org.name })));
            orgSlot.replaceChildren(ui.field("Organization", orgChoice, "This email belongs to more than one organization."));
            orgChoice.focus();
            say(notice, "Choose which organization to sign in to.", false);
          } else say(notice, error.message);
        }
      });
    });
    show("Sign in", "Sign in to your organization's Aira workspace.", form,
      swap("New organization? ", link("Create one", "#/signup")), swap(link("Forgot your password?", "#/forgot")),
      h("p", { class: "switch ca-staff" }, h("a", { href: "#/platform", text: "Aira staff" })));
  }

  function renderMfa({ gate }) {
    const code = field("Code from your authenticator app, or a recovery code", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 20, spellcheck: "false" });
    const notice = noticeNode();
    const submit = h("button", { class: "btn", type: "submit", style: { width: "100%", marginTop: "8px" }, text: "Verify" });
    const form = h("form", { novalidate: true }, code.node, notice, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await Aira.withBusy(submit, async () => {
        try {
          await api("/api/auth/mfa/verify", { method: "POST", body: { code: cleanCode(code.input.value) }, silent: true });
          reload();
        } catch (error) { say(notice, error.status === 401 ? "That code is not correct. Try the next code, or use a recovery code." : error.message); code.input.select(); }
      });
    });
    show("Two-step verification", gate ? "Your account needs a second factor before you can continue." : "Enter the 6-digit code to finish signing in.", form,
      h("p", { class: "switch" }, h("button", { type: "button", text: "Sign out", onclick: signOut })));
  }

  function renderMfaSetup() {
    const notice = noticeNode();
    const body = h("div");
    const panel = show("Set up two-step verification", "Your role requires it. Use any authenticator app (1Password, Authy, Google Authenticator, Microsoft Authenticator).", body, notice,
      h("p", { class: "switch" }, h("button", { type: "button", text: "Sign out", onclick: signOut })));
    mfaSetupFlow(body, notice, () => reload(), panel);
  }

  // Shared by the enrolment gate and the Security section.
  async function mfaSetupFlow(container, notice, done) {
    container.replaceChildren(ui.skeleton(2));
    let begun;
    try { begun = await api("/api/auth/mfa/begin", { method: "POST", body: {}, silent: true }); }
    catch (error) { container.replaceChildren(ui.error(error, () => mfaSetupFlow(container, notice, done))); return; }
    const code = field("6-digit code", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 8, spellcheck: "false" });
    const submit = h("button", { class: "btn", type: "submit", text: "Turn on" });
    const secret = h("code", { class: "ca-secret", text: groups(begun.secret), "aria-label": `Setup key ${groups(begun.secret)}` });
    const form = h("form", { novalidate: true }, code.node, h("div", { class: "btn-row", style: { marginTop: "10px" } }, submit));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await Aira.withBusy(submit, async () => {
        try {
          const enabled = await api("/api/auth/mfa/enable", { method: "POST", body: { code: cleanCode(code.input.value) }, silent: true });
          say(notice, "");
          showRecoveryCodes(container, enabled.recovery_codes, done);
        } catch (error) { say(notice, error.message); code.input.select(); }
      });
    });
    container.replaceChildren(
      h("ol", { class: "ca-steps" },
        h("li", {}, "In your authenticator app, add an account and choose ", h("strong", { text: "enter a setup key" }), "."),
        h("li", {}, "Enter this key (time-based):", h("div", { class: "ca-secret-row" }, secret, h("button", { type: "button", class: "btn secondary sm", text: "Copy", onclick: (event) => copyText(begun.secret, event.currentTarget) })),
          h("p", { class: "muted" }, "Or open ", h("a", { href: begun.otpauth_uri, text: "the setup link" }), " on this device if your app is installed.")),
        h("li", {}, "Enter the 6-digit code it shows:")),
      form);
  }

  function copyText(text, button) {
    const original = button ? button.textContent : "";
    const finish = (ok) => { if (button) { button.textContent = ok ? "Copied" : "Copy failed"; setTimeout(() => { button.textContent = original; }, 1500); } };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(() => finish(true), () => finish(false));
    else finish(false);
  }

  function download(filename, text) {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const anchor = h("a", { href: url, download: filename });
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function showRecoveryCodes(container, codes, done) {
    const text = codes.join("\n");
    const confirmed = h("input", { type: "checkbox", id: "caSaved" });
    const next = h("button", { type: "button", class: "btn", text: "Continue", disabled: true, onclick: done });
    confirmed.addEventListener("change", () => { next.disabled = !confirmed.checked; });
    container.replaceChildren(
      h("div", { class: "ca-recovery" },
        h("h2", { text: "Save your recovery codes" }),
        h("p", { class: "muted", text: "Each code works once if you lose your authenticator. They are shown only now." }),
        h("div", { class: "ca-codes", role: "list" }, ...codes.map((c) => h("code", { role: "listitem", text: c }))),
        h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary sm", text: "Copy all", onclick: (event) => copyText(text, event.currentTarget) }), h("button", { type: "button", class: "btn secondary sm", text: "Download", onclick: () => download("aira-recovery-codes.txt", `Aira recovery codes\n\n${text}\n`) })),
        h("label", { class: "ca-check", for: "caSaved" }, confirmed, h("span", { text: "I saved these codes somewhere safe" })),
        h("div", { class: "btn-row", style: { marginTop: "10px" } }, next)));
    confirmed.focus();
  }

  function renderEmailGate() {
    const me = cloud.me || {};
    const notice = noticeNode();
    const resend = h("button", { type: "button", class: "btn secondary", text: "Resend the email" });
    resend.addEventListener("click", () => Aira.withBusy(resend, async () => {
      await api("/api/auth/resend-verification", { method: "POST", body: {}, silent: true });
      say(notice, "Sent. Check your inbox (and spam).", false);
    }));
    show("Verify your email", `We sent a link to ${me.email || "your address"}. Open it to finish setting up. It works once and expires in 24 hours.`,
      notice,
      h("div", { class: "btn-row", style: { marginTop: "6px" } }, resend, h("button", { type: "button", class: "btn", text: "I verified it", onclick: reload })),
      h("p", { class: "switch" }, h("button", { type: "button", text: "Sign out", onclick: signOut })));
  }

  function renderSignup() {
    const org = field("Organization name", { type: "text", autocomplete: "organization", maxlength: 120 });
    const name = field("Your name", { type: "text", autocomplete: "name", maxlength: 120 });
    const email = field("Work email", { type: "email", autocomplete: "email", maxlength: 254 });
    const password = field("Password", { type: "password", autocomplete: "new-password", minlength: 12, maxlength: 1024 }, "At least 12 characters. A few random words work well.");
    const meter = strengthMeter(password.input, () => email.input.value);
    const notice = noticeNode();
    const submit = h("button", { class: "btn", type: "submit", style: { width: "100%", marginTop: "8px" }, text: "Create organization" });
    const trial = (cloud.config && cloud.config.trial_days) || 14;
    const form = h("form", { novalidate: true }, org.node, name.node, email.node, password.node, meter.node, notice, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!strength(password.input.value, email.input.value).ok) { say(notice, "Choose a password of at least 12 characters that does not contain your email name."); password.input.focus(); return; }
      await Aira.withBusy(submit, async () => {
        try {
          await api("/api/auth/signup-org", { method: "POST", body: { organization: org.input.value, name: name.input.value, email: email.input.value, password: password.input.value }, silent: true });
          renderCheckEmail(email.input.value);
        } catch (error) { say(notice, error.message); }
      });
    });
    show("Create your organization", `Start a ${trial}-day trial. Your organization's candidates, coaches and administrators join by invitation.`, form, swap("Already registered? ", link("Sign in", "#/login")));
  }

  function renderCheckEmail(address) {
    const notice = noticeNode();
    const resend = h("button", { type: "button", class: "btn secondary", text: "Resend the email" });
    resend.addEventListener("click", () => Aira.withBusy(resend, async () => {
      try { await api("/api/auth/resend-verification", { method: "POST", body: {}, silent: true }); say(notice, "Sent again.", false); }
      catch (error) { say(notice, error.message); }
    }));
    show("Check your email", `We sent a confirmation link to ${address}. Open it to finish setting up your organization.`, notice,
      h("div", { class: "btn-row" }, resend, h("button", { type: "button", class: "btn", text: "I confirmed it", onclick: reload })));
  }

  async function renderVerify(token) {
    show("Confirming your email", "One moment…", ui.skeleton(1));
    try {
      await api("/api/auth/verify-email", { method: "POST", body: { token }, silent: true });
      show("Email confirmed", "Thanks. Your email address is verified.", h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn", text: "Continue", onclick: () => { location.hash = ""; location.reload(); } })));
    } catch (error) {
      show("That link did not work", error.message, h("div", { class: "btn-row" }, h("a", { class: "btn", href: "#/login", text: "Go to sign in" })));
    }
  }

  function renderForgot() {
    const email = field("Email", { type: "email", autocomplete: "username", maxlength: 254 });
    const notice = noticeNode();
    const submit = h("button", { class: "btn", type: "submit", style: { width: "100%", marginTop: "8px" }, text: "Send reset link" });
    const form = h("form", { novalidate: true }, email.node, notice, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await Aira.withBusy(submit, async () => {
        try {
          const data = await api("/api/auth/password/forgot", { method: "POST", body: { email: email.input.value }, silent: true });
          say(notice, data.message || "If that address belongs to an account, a reset link is on its way.", false);
        } catch (error) { say(notice, error.message); }
      });
    });
    show("Reset your password", "Enter your email. If it belongs to an account, we will send a link that works once.", form, swap(link("Back to sign in", "#/login")));
  }

  function renderReset(token) {
    const password = field("New password", { type: "password", autocomplete: "new-password", minlength: 12, maxlength: 1024 }, "At least 12 characters.");
    const meter = strengthMeter(password.input);
    const notice = noticeNode();
    const submit = h("button", { class: "btn", type: "submit", style: { width: "100%", marginTop: "8px" }, text: "Set new password" });
    const form = h("form", { novalidate: true }, password.node, meter.node, notice, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await Aira.withBusy(submit, async () => {
        try {
          await api("/api/auth/password/reset", { method: "POST", body: { token, password: password.input.value }, silent: true });
          show("Password updated", "You were signed out everywhere. Sign in with your new password.", h("div", { class: "btn-row" }, h("a", { class: "btn", href: "#/login", text: "Sign in" })));
        } catch (error) { say(notice, error.message); }
      });
    });
    show("Choose a new password", "This link works once.", form);
  }

  async function renderInvite(token) {
    show("Your invitation", "One moment…", ui.skeleton(1));
    let info;
    try { info = await api(`/api/invites/${encodeURIComponent(token)}`, { silent: true }); }
    catch (error) { show("That invitation is not available", error.message, h("div", { class: "btn-row" }, h("a", { class: "btn", href: "#/login", text: "Go to sign in" }))); return; }
    const roleText = { owner: "an owner", admin: "an administrator", coach: "a coach", candidate: "a candidate", billing: "the billing contact", auditor: "an auditor" }[info.role] || info.role;
    const name = field("Your name", { type: "text", autocomplete: "name", maxlength: 120 });
    const password = field("Choose a password", { type: "password", autocomplete: "new-password", minlength: 12, maxlength: 1024 }, "At least 12 characters.");
    const meter = strengthMeter(password.input, () => info.email);
    const notice = noticeNode();
    const submit = h("button", { class: "btn", type: "submit", style: { width: "100%", marginTop: "8px" }, text: "Join" });
    const form = h("form", { novalidate: true }, name.node, password.node, meter.node, notice, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!strength(password.input.value, info.email).ok) { say(notice, "Choose a password of at least 12 characters that does not contain your email name."); return; }
      await Aira.withBusy(submit, async () => {
        try {
          await api(`/api/invites/${encodeURIComponent(token)}/accept`, { method: "POST", body: { name: name.input.value, password: password.input.value }, silent: true });
          location.hash = "";
          location.reload();
        } catch (error) { say(notice, error.message); }
      });
    });
    show(`Join ${info.org.name}`, `You were invited as ${roleText}.`,
      ui.kv([["Organization", info.org.name], ["Email", info.email], ["Role", info.role]]), form);
  }

  // -------------------------------------------------------------------------
  // Entry points the core and app.js use
  // -------------------------------------------------------------------------
  auth.render = (screen) => {
    const anonymous = parseAnonymous(location.hash);
    const target = screen || (anonymous && anonymous.screen) || "login";
    const token = anonymous ? anonymous.token : "";
    ({ login: renderLogin, signup: renderSignup, forgot: renderForgot, verify: () => renderVerify(token), reset: () => renderReset(token), invite: () => renderInvite(token) }[target] || renderLogin)();
  };

  cloud.showAuth = () => {
    if (String(location.hash).startsWith("#/platform")) return; // the platform console has its own sign-in
    auth.render();
  };

  cloud.showGate = (code) => {
    if (code === "email_unverified") renderEmailGate();
    else if (code === "mfa_required") renderMfa({ gate: true });
    else if (code === "mfa_enrollment_required") renderMfaSetup();
  };

  // Block the app up front when the account still owes a verification or a second factor.
  const coreBoot = cloud.boot;
  cloud.boot = async (user) => {
    if (user.kind === "member") {
      const anonymous = parseAnonymous(location.hash);
      if (anonymous && anonymous.screen !== "login") { cloud.me = user; auth.render(); return false; }
      if (user.verified === false) { cloud.me = user; gated = true; renderEmailGate(); return false; }
      if (user.mfa && user.mfa.enabled && !user.mfa.verified) { cloud.me = user; gated = true; renderMfa({ gate: true }); return false; }
      if (user.mfa && user.mfa.required && !user.mfa.enabled) { cloud.me = user; gated = true; renderMfaSetup(); return false; }
    }
    gated = false;
    try {
      return await coreBoot(user);
    } catch (error) {
      // cloud-core.js reads `$` from window, where app.js's `const $` is not; finish its boot here until that is fixed there.
      if (!(error instanceof TypeError) || !/\$ is not a function/.test(error.message)) throw error;
      document.getElementById("accountEmail").textContent = `${user.email} · ${user.role}${user.org ? " · " + user.org.name : ""}`;
      cloud.buildNav();
      cloud.refreshBanners();
      if (!cloud.bannerTimer) cloud.bannerTimer = setInterval(cloud.refreshBanners, 60000);
      return cloud.hasWorkspace();
    }
  };

  const baseLanding = cloud.landing;
  cloud.landing = (workspace) => (gated ? ["gate"] : baseLanding(workspace));

  // Anonymous routes (#/invite/…, #/forgot, …) follow the hash while the auth screen or a signed-in app is showing.
  window.addEventListener("hashchange", () => {
    const anonymous = parseAnonymous(location.hash);
    if (!anonymous) return;
    if (anonymous.screen === "login" && !$("authScreen").hidden) return auth.render();
    auth.render();
  });

  // -------------------------------------------------------------------------
  // LinkedIn warning: the connect call is held until the candidate has seen the User Agreement notice
  // -------------------------------------------------------------------------
  let noticeText = "";
  async function linkedinNotice() {
    if (noticeText) return noticeText;
    try { noticeText = (await api("/api/me/linkedin-notice", { silent: true })).notice; } catch { noticeText = ""; }
    return noticeText || FALLBACK_NOTICE;
  }
  auth.confirmLinkedIn = async (confirmLabel = "I understand, continue") => {
    const text = await linkedinNotice();
    return new Promise((resolve) => {
      modal({
        title: "Before you connect LinkedIn",
        body: h("div", {}, h("p", { text }), h("p", { class: "muted", text: "This is optional. Aira works without LinkedIn, and you can turn it off at any time." })),
        actions: [{ label: "Cancel", kind: "ghost", onClick: () => resolve(false) }, { label: confirmLabel, onClick: () => resolve(true) }],
        onClose: () => resolve(false),
      });
    });
  };

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input && input.url;
    // The Aira API console belongs to the organization's administrators; answer for everyone else without a request.
    if (url && url.startsWith("/api/airaapi/") && cloud.me && cloud.me.kind === "member" && !cloud.can("integrations.read")) {
      return new Response(JSON.stringify({ error: "The Aira API console belongs to your organization's administrators.", code: "forbidden" }), { status: 403, headers: { "Content-Type": "application/json" } });
    }
    if (url === "/api/linkedin/connect" && init && init.method === "POST" && cloud.me && cloud.me.role === "candidate") {
      let body = {};
      try { body = JSON.parse(init.body || "{}"); } catch { body = {}; }
      if (body.acknowledge !== true) {
        const already = cloud.me.workspace && cloud.me.workspace.linkedin_opted_in;
        if (!already && !(await auth.confirmLinkedIn())) {
          return new Response(JSON.stringify({ error: "LinkedIn was not connected.", code: "cancelled" }), { status: 400, headers: { "Content-Type": "application/json" } });
        }
        init = { ...init, body: JSON.stringify({ ...body, acknowledge: true }) };
        if (cloud.me.workspace) cloud.me.workspace.linkedin_opted_in = true;
      }
    }
    return nativeFetch(input, init);
  };

  // A coach answering a sensitive question gets a plain explanation, not a generic error.
  const reportError = Aira.reportError;
  Aira.reportError = (error) => {
    if (error && error.code === "sensitive_answer") {
      Aira.toast("Only the candidate can answer this question. It asks for sensitive information such as work authorization.", "error", { timeout: 9000 });
      return;
    }
    reportError(error);
  };

  // -------------------------------------------------------------------------
  // Security section
  // -------------------------------------------------------------------------
  const ALL_ROLES = ["owner", "admin", "coach", "candidate", "billing", "auditor"];

  async function securityView(stage) {
    const [mfa, sessions] = await Promise.all([api("/api/auth/mfa"), api("/api/auth/sessions")]);
    const me = cloud.me;

    // Profile
    const nameInput = h("input", { type: "text", value: me.name || "", maxlength: 120, autocomplete: "name" });
    const profileForm = h("form", { class: "panel-body", novalidate: true }, ui.field("Your name", nameInput), ui.kv([["Email", me.email], ["Role", me.role], ["Organization", me.org && me.org.name], ["Signed in with", me.auth_method]]),
      h("div", { class: "btn-row", style: { marginTop: "10px" } }, h("button", { class: "btn", type: "submit", text: "Save name" })));
    profileForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      await Aira.withBusy(event.submitter || profileForm.querySelector("button"), async () => {
        const updated = await api("/api/auth/profile", { method: "PATCH", body: { name: nameInput.value } });
        cloud.me = { ...cloud.me, ...updated };
        Aira.toast("Name saved.", "success");
      });
    });

    // Password
    const current = h("input", { type: "password", autocomplete: "current-password", maxlength: 1024 });
    const next = h("input", { type: "password", autocomplete: "new-password", minlength: 12, maxlength: 1024 });
    const meter = strengthMeter(next, () => me.email);
    const passwordForm = h("form", { class: "panel-body", novalidate: true }, ui.field("Current password", current), ui.field("New password", next, "At least 12 characters. Changing it signs you out of your other devices."), meter.node,
      h("div", { class: "btn-row", style: { marginTop: "10px" } }, h("button", { class: "btn", type: "submit", text: "Change password" })));
    passwordForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!strength(next.value, me.email).ok) { Aira.toast("Choose a password of at least 12 characters that does not contain your email name.", "error"); return; }
      await Aira.withBusy(passwordForm.querySelector("button"), async () => {
        await api("/api/auth/password/change", { method: "POST", body: { current: current.value, password: next.value } });
        current.value = ""; next.value = ""; meter.update();
        Aira.toast("Password changed. Your other devices were signed out.", "success");
        router.render();
      });
    });

    // MFA
    const mfaBody = h("div", { class: "panel-body" });
    const mfaPanel = h("div", { class: "panel", id: "securityMfa" }, h("div", { class: "panel-head" }, h("h3", { text: "Two-step verification" }), h("span", { class: `badge ${mfa.enabled ? "stage applied" : ""}`, text: mfa.enabled ? "On" : "Off" })), mfaBody);
    const mfaNotice = noticeNode();
    if (!mfa.enabled) {
      const start = h("button", { type: "button", class: "btn", text: "Set up two-step verification" });
      start.addEventListener("click", () => { mfaBody.replaceChildren(h("div"), mfaNotice); mfaSetupFlow(mfaBody.firstChild, mfaNotice, () => router.render()); });
      mfaBody.append(h("p", { class: "muted", text: mfa.required ? "Your role requires it on this plan." : "Adds a code from your phone to every sign-in." }), h("div", { class: "btn-row" }, start));
    } else if (!mfa.can_disable) {
      mfaBody.append(h("p", { text: "Two-step verification is on." }), h("p", { class: "muted", text: "Required for your role on this plan, so it cannot be turned off." }));
    } else {
      const password = h("input", { type: "password", autocomplete: "current-password" });
      const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 20 });
      const off = h("form", { novalidate: true }, h("p", { text: "Two-step verification is on." }), ui.field("Password", password), ui.field("Code from your app", code), mfaNotice, h("div", { class: "btn-row", style: { marginTop: "10px" } }, h("button", { class: "btn danger secondary", type: "submit", text: "Turn off" })));
      off.addEventListener("submit", async (event) => {
        event.preventDefault();
        await Aira.withBusy(off.querySelector("button"), async () => {
          try { await api("/api/auth/mfa/disable", { method: "POST", body: { password: password.value, code: cleanCode(code.value) }, silent: true }); Aira.toast("Two-step verification is off.", "success"); router.render(); }
          catch (error) { say(mfaNotice, error.message); }
        });
      });
      mfaBody.append(off);
    }

    // Sessions
    const rows = sessions.sessions.map((s) => h("div", { class: "row" },
      h("div", {}, h("span", { class: "title", text: `${s.current ? "This device" : "Another device"} · ${s.auth_method}` }), h("span", { class: "sub", text: [s.user_agent ? s.user_agent.slice(0, 80) : "", s.ip, `active ${Aira.fmt.ago(s.last_seen_at)}`].filter(Boolean).join(" · ") })),
      s.current ? h("span", { class: "badge stage applied", text: "Current" }) : h("button", { type: "button", class: "btn secondary sm", text: "Sign out", onclick: (event) => Aira.withBusy(event.currentTarget, async () => { await api(`/api/auth/sessions/${s.session_id}`, { method: "DELETE" }); router.render(); }) })));
    const others = sessions.sessions.filter((s) => !s.current).length;
    const sessionPanel = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Where you are signed in" }),
      h("button", { type: "button", class: "btn secondary sm", text: "Sign out other devices", disabled: !others, onclick: (event) => Aira.withBusy(event.currentTarget, async () => { const r = await api("/api/auth/sessions/sign-out-others", { method: "POST", body: {} }); Aira.toast(`Signed out ${r.signed_out} device${r.signed_out === 1 ? "" : "s"}.`, "success"); router.render(); }) })),
      h("div", { class: "list" }, ...rows));

    stage.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Security" }), h("p", { text: "Your sign-in, second factor and devices." }))),
      h("div", { class: "grid cols-2", style: { alignItems: "start" } },
        h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Profile" })), profileForm),
        h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Password" })), passwordForm),
        mfaPanel, sessionPanel));
  }
  const router = Aira.router;

  cloud.register({ id: "security", label: "Security", group: "Account", key: "e", icon: "settings", roles: ALL_ROLES, view: securityView });
})();

/* Aira — sign in with your Aira account (the single-user build opened from the hosted account).
 *
 * Self-contained: it adds one button to the local sign-in screen when this installation is linked to an account
 * (GET /api/auth/account-link says so), and it finishes a launch when the page opens at #/launch?token=... by
 * posting the one-time token to POST /api/auth/launch and reloading into the app. In the hosted build the
 * account-link route is not available, so nothing here runs.
 */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const LAUNCH = /^#\/launch\?(.*)$/;

  async function call(path, options = {}) {
    const response = await fetch(path, {
      method: options.method || "GET",
      headers: { "X-Requested-With": "aira", ...(options.body ? { "Content-Type": "application/json" } : {}) },
      body: options.body ? JSON.stringify(options.body) : undefined,
      credentials: "same-origin",
    });
    let data = {};
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) {
      const error = new Error(data.error || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function notice(text) {
    const target = $("authNotice");
    if (target) target.textContent = text;
  }

  function launchUrl(accountUrl) {
    const back = encodeURIComponent(`${location.origin}/`);
    return `${accountUrl.replace(/\/+$/, "")}/api/launch/local?return=${back}`;
  }

  function addButton(info) {
    const form = $("authForm");
    if (!form || $("accountLinkBtn")) return;
    const button = document.createElement("button");
    button.id = "accountLinkBtn";
    button.type = "button";
    button.className = "btn secondary";
    button.style.width = "100%";
    button.style.marginTop = "10px";
    button.textContent = "Sign in with your Aira account";
    button.addEventListener("click", () => { location.href = launchUrl(info.url); });
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.id = "accountLinkHint";
    hint.style.margin = "8px 0 0";
    hint.textContent = `Your Aira account at ${info.url} opens this installation without a second password.`;
    const anchor = $("authSubmit");
    if (anchor && anchor.parentNode === form) { form.insertBefore(button, anchor.nextSibling); form.insertBefore(hint, button.nextSibling); } else { form.append(button, hint); }
  }

  async function finishLaunch() {
    const found = LAUNCH.exec(location.hash || "");
    if (!found) return false;
    const token = new URLSearchParams(found[1]).get("token") || "";
    // the token must not stay in the address bar or the history
    history.replaceState(null, "", `${location.pathname}${location.search}#/`);
    if (typeof Aira !== "undefined" && Aira.showAuth) Aira.showAuth();
    notice("Signing in with your Aira account…");
    try {
      const data = await call("/api/auth/launch", { method: "POST", body: { token } });
      notice(`Signed in as ${data.email}. Opening Aira…`);
      location.reload();
      return true;
    } catch (error) {
      notice(error.status === 502 ? `${error.message} Check that both sides share the same secret.` : error.message);
      return false;
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    if (typeof Aira !== "undefined" && Aira.cloud) return; // the hosted build is the account itself: nothing to add
    let info = { available: false, url: "" };
    try { info = await call("/api/auth/account-link"); } catch { return; }
    if (info.available && info.url) addButton(info);
    await finishLaunch();
  });
})();

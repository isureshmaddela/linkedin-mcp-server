/* Aira — opening this installation from the Aira account (the hosted build).
 *
 * Self-contained: when the page opens at #/launch?token=... (the account's app launcher sends people here) it
 * posts the one-time token to POST /api/auth/launch and reloads into the app. The local sign-in screen offers
 * only the email and password sign-in. In the hosted build nothing here runs.
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
    await finishLaunch();
  });
})();

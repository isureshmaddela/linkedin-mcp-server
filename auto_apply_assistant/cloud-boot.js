/* Aira hosted build — start-up: the platform console route, the hosted configuration and the page title. */
"use strict";

(function () {
  const isPlatform = () => String(location.hash).startsWith("#/platform");

  // Keep the organization's name in the tab title: "Jobs · Northwind · Aira".
  let titleGuard = false;
  function watchTitle() {
    const node = document.querySelector("title");
    if (!node) return;
    new MutationObserver(() => {
      if (titleGuard) return;
      const org = Aira.cloud && Aira.cloud.me && Aira.cloud.me.org && Aira.cloud.me.org.name;
      if (!org || document.title.includes(org) || !document.title.endsWith("· Aira")) return;
      titleGuard = true;
      document.title = document.title.replace(/· Aira$/, `· ${org} · Aira`);
      titleGuard = false;
    }).observe(node, { childList: true });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    watchTitle();
    if (isPlatform()) {
      // The platform console is a separate surface with its own sign-in; it defines platformBoot.
      if (Aira.cloud.platformBoot) Aira.cloud.platformBoot();
      return;
    }
    try { Aira.cloud.config = await Aira.api("/api/cloud/config", { silent: true }); } catch { Aira.cloud.config = null; }
  });

  // Moving between the member app and the platform console is a different application surface: start it cleanly.
  let wasPlatform = isPlatform();
  window.addEventListener("hashchange", () => {
    const now = isPlatform();
    if (now !== wasPlatform) {
      wasPlatform = now;
      if (now && Aira.cloud.platformBoot) Aira.cloud.platformBoot();
      else if (!now) location.reload();
    }
  });
})();

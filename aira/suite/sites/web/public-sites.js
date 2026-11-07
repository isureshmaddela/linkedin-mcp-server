/* Aira — Sites' public links (aira/suite/sites).
 *
 * preview and site: a website built in Sites. Its files are served under /api/public/<token>/w/ with their own
 * sandboxed page policy, so the short /p/<token> address only sends the visitor there.
 */
"use strict";
(() => {
  const open = (root, data, ctx) => {
    const url = data.page && data.page.url;
    if (url && /^\/api\/public\/[A-Za-z0-9_-]{40,64}\/w\/$/.test(url)) {
      location.replace(url);
      root.append(ctx.h("p", { class: "pub-muted", text: "Opening the site…" }));
      return;
    }
    root.append(ctx.h("p", { class: "pub-muted", text: "This site is not available." }));
  };
  window.AiraPublic.register("sites", "preview", open);
  window.AiraPublic.register("sites", "site", open);
})();

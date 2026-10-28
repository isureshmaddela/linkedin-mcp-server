/* Aira — the public copyright notice form for Videos (/videos/copyright; aira/suite/videos/service.py file_notice).
 *
 * Anyone can use it without an account: a copyright owner (or someone acting for them) names the video, the work it
 * copies, and signs two statements. The notice goes to Aira's moderators (hosted) or the computer's owner, who can
 * take the video down; its uploader can then send a counter-notice. DOM is built with textContent only.
 */
"use strict";
(() => {
  const main = document.getElementById("pubMain");
  const h = (tag, attrs, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "text") node.textContent = String(value);
      else if (key === "class") node.className = value;
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (value === true) node.setAttribute(key, "");
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat()) if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return node;
  };
  const field = (label, input, hint) => h("div", { class: "pub-field" }, h("label", { for: input.id, text: label }), input, hint ? h("span", { class: "pub-muted", text: hint }) : null);
  // A checkbox beside its sentence (the form's field style stretched a box across the page above the words).
  const statement = (box, text) => {
    const label = h("label", { for: box.id }, box, h("span", { text }));
    Object.assign(label.style, { display: "flex", gap: "12px", alignItems: "flex-start", margin: "4px 0 14px", cursor: "pointer" });
    Object.assign(box.style, { flex: "none", width: "24px", height: "24px", minHeight: "0", margin: "2px 0 0", padding: "0" });
    return label;
  };
  const params = new URLSearchParams(location.search);

  const video = h("input", { id: "cpVideo", name: "video", required: true, value: params.get("video") || "", placeholder: "The video's address, or the id shown under it" });
  const name = h("input", { id: "cpName", name: "name", required: true, autocomplete: "name" });
  const email = h("input", { id: "cpEmail", name: "email", type: "email", required: true, autocomplete: "email" });
  const organization = h("input", { id: "cpOrg", name: "organization", placeholder: "Leave empty if the work is yours" });
  const work = h("textarea", { id: "cpWork", name: "work", required: true, rows: "4", placeholder: "Describe your work (title, where it was published) and what the video copies." });
  const goodFaith = h("input", { id: "cpGoodFaith", type: "checkbox" });
  const accurate = h("input", { id: "cpAccurate", type: "checkbox" });
  const signature = h("input", { id: "cpSignature", name: "signature", required: true, placeholder: "Type your full name" });
  const notice = h("div", { role: "status" });
  const send = h("button", { type: "submit", class: "pub-btn", text: "Send notice" });

  const form = h("form", { class: "pub-form pub-card", onsubmit: async (event) => {
    event.preventDefault();
    notice.replaceChildren();
    send.disabled = true;
    try {
      const response = await fetch("/api/videos/copyright", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "aira" },
        credentials: "same-origin",
        body: JSON.stringify({ video: video.value, name: name.value, email: email.value, organization: organization.value, work: work.value, signature: signature.value, good_faith: goodFaith.checked, accurate: accurate.checked }),
      });
      let data = {};
      try { data = await response.json(); } catch { data = {}; }
      if (!response.ok) throw new Error(data.error || `The notice could not be sent (${response.status}).`);
      form.replaceWith(h("div", { class: "pub-card" }, h("h2", { text: "Thank you. Your notice was received." }), h("p", { text: "A moderator reviews it. If the video copies your work, it is taken down and its uploader is told; they may answer with a counter-notice." }), h("p", { class: "pub-muted", text: `Notice number: ${data.notice.id}` })));
    } catch (error) {
      notice.replaceChildren(h("div", { class: "pub-notice bad", role: "alert", text: error.message }));
      send.disabled = false;
    }
  } },
    h("h1", { text: "Report a copyright problem" }),
    h("p", { text: "Use this form if a video on Monosage copies a film, song, picture or other work that you own, or that you may act for. You do not need an account." }),
    field("Which video?", video),
    field("Your full name", name),
    field("Your email address", email, "We write to you about this notice only."),
    field("Who you act for", organization),
    field("Your work", work),
    statement(goodFaith, "I believe in good faith that this use of the work is not allowed by its owner, its agent or the law."),
    statement(accurate, "This notice is accurate, and I own the work or may act for its owner."),
    field("Signature", signature),
    notice,
    h("div", { class: "pub-btn-row" }, send),
    h("p", { class: "pub-muted", text: "A false notice can have legal consequences. The uploader sees the work you describe and your name, not your email address." }),
  );
  document.title = "Copyright notice · Monosage Videos";
  main.replaceChildren(form);
})();

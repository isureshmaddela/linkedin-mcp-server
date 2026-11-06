/* Aira — Social (#/social): Aira's own social network and messenger, safe for every age.
 *
 * Sections (?tab=), each a folder of the frame's left menu (a rail with icons and counts on a wide screen) and, on a
 * phone, a bottom bar of the five most used with "More" for the rest:
 *   feed (Home: stories, "On this day", the composer and posts; ?post=, ?tag=), explore (popular posts, people and
 *   #tags, search across all of them; ?q=), reels (tall videos from the Videos app), chats (?chat=), friends (search,
 *   requests, close friends, suggestions, blocked people), dating (adults: views-social-dating.js, Aira.socialDating),
 *   groups (?group=), pages (?page=), market (the Marketplace; ?listing=, ?view=selling|saved), notifications, saved
 *   (collections; ?collection=), profile (yours or ?person=; ?view=posts|photos|reels|tagged) and, for the
 *   household's owner, moderation. A grown-up also says yes or no to the household's children's new friends.
 * "+ Create" (page head) makes a post, a photo post, a poll, a story, a reel (Videos' uploader), a listing, a group or
 * a page. Photos come from the device: the browser shrinks them (which also drops their metadata), uploads them to
 * /api/social/photos and attaches them by reference; the server removes what metadata is left.
 * Everything runs through /api/social/* (aira/suite/social/social_routes.py) as the person using Aira (life.js).
 * Live pages poll with Aira.suite.live and a since= time. DOM is built with h() and textContent only.
 *
 * Dating lives in its own file: Aira.socialDating.available(age_group) says whether to offer it and
 * Aira.socialDating.tab(panel, route, redraw) draws it. Aira.socialKit hands it this file's helpers.
 *
 * In the Aira frame (shell.js): "+ New" (Aira.appNew.social) asks with ?new=post|photo|poll|story|chat|group|page|
 * listing and the page opens that form once, then drops new= from the address; the search box across the top
 * (Aira.appSearch.social) finds people, chats, groups, pages, Marketplace listings and family members and events,
 * and Enter lists them on ?tab=search&q=.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("social", path, opts);
  const go = (query) => Aira.router.go(["social"], query);
  // Children of a node without the empty ones (append(null) would print "null").
  const kids = (...nodes) => nodes.flat().filter((node) => node !== null && node !== undefined && node !== false && node !== "");

  const REACTIONS = ["❤️", "👍", "😂", "😮", "😢", "🎉"];
  const REACTION_NAMES = { "❤️": "love", "👍": "like", "😂": "laugh", "😮": "wow", "😢": "sad", "🎉": "celebrate" };
  const STORY_REACTIONS = ["❤️", "😂", "😮", "😢", "👏", "🔥"];
  const AUDIENCE_LABELS = { public: "Everyone", friends: "Friends", close_friends: "Close friends", only_me: "Only me", group: "Group", page: "Page" };
  const AUDIENCE_ICONS = { public: "🌍", friends: "👥", close_friends: "⭐", only_me: "🔒", group: "👥", page: "🏷️" };
  const REPORT_LABELS = { post: "post", comment: "comment", message: "message", account: "person", story: "story", listing: "listing" };
  // A feeling or activity beside a post (the server keeps the key).
  const FEELINGS = {
    happy: ["😊", "feeling happy"], grateful: ["🙏", "feeling grateful"], excited: ["🤩", "feeling excited"], loved: ["🥰", "feeling loved"],
    proud: ["😌", "feeling proud"], relaxed: ["😎", "feeling relaxed"], tired: ["😴", "feeling tired"], sad: ["😢", "feeling sad"],
    celebrating: ["🎉", "celebrating"], eating: ["🍽️", "eating out"], travelling: ["✈️", "travelling"], watching: ["🎬", "watching a film"],
    reading: ["📖", "reading"], listening: ["🎧", "listening to music"], playing: ["🎮", "playing a game"], working: ["💼", "working"],
    exercising: ["🏃", "working out"], cooking: ["🍳", "cooking"],
  };
  // Photo filters: the photos stay as sent; a CSS filter by this name shows them warmer, cooler, in black and white...
  const FILTERS = [["", "Original"], ["warm", "Warm"], ["cool", "Cool"], ["vivid", "Vivid"], ["mono", "Black and white"], ["fade", "Faded"], ["vintage", "Vintage"], ["dramatic", "Dramatic"]];
  const PAGE_SIZE = 30;
  const CATEGORY_LABELS = { home: "Home and furniture", clothes: "Clothes and shoes", kids: "Kids and baby", electronics: "Phones and electronics", vehicles: "Bikes and cars", sports: "Sports and outdoors", books: "Books, films and music", garden: "Garden and DIY", services: "Services and lessons", other: "Other" };
  const CONDITION_LABELS = { new: "New", like_new: "Like new", good: "Good", used: "Used", for_parts: "For parts" };
  const STATUS_LABELS = { available: "For sale", reserved: "Reserved", sold: "Sold" };
  const young = (me) => me.age_group === "child" || me.age_group === "teen";
  const isPicture = (src) => /^https:\/\//i.test(src || "") || /^\/api\/(social|public)\//.test(src || "");

  // Glyphs the shared set lacks, for the menu's folders, the bottom bar, "+ Create" and the post actions (added once).
  const GLYPHS = {
    soPhoto: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5L6 20"/></svg>',
    soPen: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/></svg>',
    soStory: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke-dasharray="4 2.2"/><path d="M12 8v8M8 12h8"/></svg>',
    soGroup: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3"/><circle cx="5" cy="10" r="2.2"/><circle cx="19" cy="10" r="2.2"/><path d="M6.5 20a5.5 5.5 0 0 1 11 0M1.5 19a3.6 3.6 0 0 1 4.5-3.4M22.5 19a3.6 3.6 0 0 0-4.5-3.4"/></svg>',
    soFlag: '<svg viewBox="0 0 24 24"><path d="M5 21V4"/><path d="M5 4h12l-2 4 2 4H5"/></svg>',
    soTag: '<svg viewBox="0 0 24 24"><path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.5"/></svg>',
    soPerson: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    soBell: '<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/></svg>',
    soShield: '<svg viewBox="0 0 24 24"><path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>',
    soHome: '<svg viewBox="0 0 24 24"><path d="M3 11 12 4l9 7"/><path d="M5 10v10h5v-6h4v6h5V10"/></svg>',
    soCompass: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/></svg>',
    soReel: '<svg viewBox="0 0 24 24"><rect x="5" y="2.5" width="14" height="19" rx="3"/><path d="m10.5 9 4 3-4 3z"/></svg>',
    soHeart: '<svg viewBox="0 0 24 24"><path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/></svg>',
    soBookmark: '<svg viewBox="0 0 24 24"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
    soPlus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
    soCamera: '<svg viewBox="0 0 24 24"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>',
    soPoll: '<svg viewBox="0 0 24 24"><path d="M5 20V10M12 20V4M19 20v-7"/></svg>',
    soSmile: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8.5 14a4.5 4.5 0 0 0 7 0"/><path d="M9 9.5h.01M15 9.5h.01"/></svg>',
    soPin: '<svg viewBox="0 0 24 24"><path d="M12 21s-6-5.4-6-11a6 6 0 0 1 12 0c0 5.6-6 11-6 11z"/><circle cx="12" cy="10" r="2.2"/></svg>',
    soDots: '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>',
    soChat: '<svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4A8 8 0 1 1 20 12z"/></svg>',
    soShare: '<svg viewBox="0 0 24 24"><path d="M4 12v7h16v-7"/><path d="M12 3v12M7.5 7.5 12 3l4.5 4.5"/></svg>',
    soStar: '<svg viewBox="0 0 24 24"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/></svg>',
    soSend: '<svg viewBox="0 0 24 24"><path d="M21 3 3 10.5l7 3 3 7z"/><path d="m10 13.5 4-4"/></svg>',
    soLeft: '<svg viewBox="0 0 24 24"><path d="m15 5-7 7 7 7"/></svg>',
    soRight: '<svg viewBox="0 0 24 24"><path d="m9 5 7 7-7 7"/></svg>',
    soSound: '<svg viewBox="0 0 24 24"><path d="M4 10v4h4l5 4V6L8 10z"/><path d="M16 9a4 4 0 0 1 0 6"/></svg>',
    soMute: '<svg viewBox="0 0 24 24"><path d="M4 10v4h4l5 4V6L8 10z"/><path d="m16 10 4 4M20 10l-4 4"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;
  // Each section is a folder in the frame's left menu; its label names the page and `about` is the line under it.
  const TAB_META = {
    feed: { label: "Home", icon: "soHome", about: "Stories, posts and news from your friends, groups and pages." },
    explore: { label: "Explore", icon: "soCompass", about: "What is popular now, people to follow and #tags to look through." },
    reels: { label: "Reels", icon: "soReel", about: "Short tall videos from the Videos app, one after another." },
    chats: { label: "Chats", icon: "social", about: "Private chats with one friend or a group." },
    friends: { label: "Friends", icon: "recruiters", about: "Find people you know, answer requests and see who you may know." },
    groups: { label: "Groups", icon: "soGroup", about: "Groups you belong to, and public groups to join." },
    pages: { label: "Pages", icon: "soFlag", about: "Shops, clubs and services you follow, and the pages you run." },
    market: { label: "Marketplace", icon: "soTag", about: "Buy from people near you, and sell what you no longer need." },
    notifications: { label: "Notifications", icon: "soBell", about: "Reactions, comments, replies, mentions, follows and requests." },
    saved: { label: "Saved", icon: "soBookmark", about: "Posts you saved, in your own collections." },
    profile: { label: "Profile", icon: "soPerson", about: "How others see you on Social." },
    moderation: { label: "Moderation", icon: "soShield", about: "Reports from your household and the words Social hides." },
    // Dating (views-social-dating.js, adults only): drawn by Aira.socialDating.tab.
    dating: { label: "Dating", icon: "sdHeart", about: "Meet new people: adults only, with a separate dating profile." },
  };
  // The phone's bottom bar: the five most used; everything else is under More.
  const BOTTOM = ["feed", "explore", "reels", "chats", "notifications"];
  const BOTTOM_SHORT = { notifications: "Alerts" }; // the bar is narrow; the full name is read out
  // What "+ New" asked for (?new=), answered once the page is drawn.
  let pending = "";
  // The Social account of the person using Aira, kept from the page's last GET /me ("+ New" and search read it).
  let known = null;
  // Children, then teens: what "+ New" and the search box leave out, like the pages do.
  const ageGroup = () => (Aira.life && Aira.life.me && Aira.life.me.age_group) || (known && known.age_group) || "adult";
  const familyOpen = () => (Aira.visibleNav ? Aira.visibleNav() : []).some((item) => item.id === "family") && !(Aira.apps && Aira.apps.blocked && Aira.apps.blocked("family"));
  // Poll while this screen is on the page. (Aira.suite.live alone keeps going while the person moves between tabs
  // of Social, because tabs live in the query: every visit would add another loop.)
  // With topics, the live stream (realtime.js) refreshes it when something changes and `ms` is the fallback poll.
  const live = (node, fn, ms, topics) => {
    if (topics && Aira.realtime) return Aira.realtime.live(topics, fn, ms, node);
    const stop = S.live(async () => { if (!node.isConnected) { stop(); return; } await fn(); }, ms);
    return stop;
  };

  const face = (person, size = 40) => {
    const avatar = (person && person.avatar) || "🙂";
    const box = { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.55)}px` };
    if (isPicture(avatar)) return h("img", { class: "so-face", src: avatar, alt: "", referrerpolicy: "no-referrer", loading: "lazy", style: box });
    return h("span", { class: "so-face", "aria-hidden": "true", style: box, text: avatar });
  };
  const when = (iso) => {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const minutes = Math.round((Date.now() - date.getTime()) / 60000);
    if (minutes < 1) return "Just now";
    if (minutes < 60) return minutes === 1 ? "1 minute ago" : `${minutes} minutes ago`;
    if (date.toDateString() === new Date().toDateString()) return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  };
  const personLink = (person) => h("a", { href: `#/social?tab=profile&person=${person.id}`, class: "so-name", text: person.name });
  const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const short = (n) => (n >= 1000000 ? `${(n / 1000000).toFixed(1).replace(/\.0$/, "")}M` : n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, "")}K` : String(n || 0));

  // Text with #tags and @mentions as links (built as text nodes, never HTML).
  const richText = (text) => {
    const node = h("p", { class: "so-text" });
    const pattern = /(#[\p{L}\p{N}_]{2,40}|@[a-z][a-z0-9_.]{2,29})/giu;
    let last = 0;
    for (const match of String(text || "").matchAll(pattern)) {
      if (match.index > last) node.append(document.createTextNode(text.slice(last, match.index)));
      const word = match[0];
      node.append(word.startsWith("#")
        ? h("a", { href: `#/social?tab=feed&tag=${encodeURIComponent(word.slice(1).toLowerCase())}`, text: word })
        : h("a", { href: `#/social?tab=friends&q=${encodeURIComponent(word.slice(1))}`, text: word }));
      last = match.index + word.length;
    }
    if (last < String(text || "").length) node.append(document.createTextNode(text.slice(last)));
    return node;
  };
  const photoList = (value) => String(value || "").split(/\s+/).filter(Boolean);

  const report = (targetType, targetId) => S.edit({
    title: `Report this ${REPORT_LABELS[targetType]}`,
    fields: [{ name: "reason", label: "What is wrong?", type: "textarea", rows: 3, placeholder: "It is unkind, unsafe, a scam or not true." }],
    submitLabel: "Send report",
    onSave: async (values) => { await api("/reports", { method: "POST", body: { target_type: targetType, target_id: targetId, reason: values.reason } }); Aira.toast("Thank you. Your report was sent to the moderators.", "success"); },
  });
  const block = async (person) => {
    if (!(await Aira.confirm({ title: `Block ${person.name}?`, message: `${person.name} cannot see your profile or posts, message you or find you. You stop being friends.`, confirmLabel: "Block", danger: true }))) return false;
    await api(`/people/${person.id}/block`, { method: "POST", body: {} });
    Aira.toast(`${person.name} is blocked.`, "success");
    return true;
  };
  const message = async (person) => {
    const created = await api("/chats", { method: "POST", body: { person_id: person.id } });
    go({ tab: "chats", chat: created.chat.id });
  };

  // A small menu under a button ("⋯" on a post): Escape or a click elsewhere closes it, arrows move between items.
  function menuButton(label, items, { icon = "soDots", kind = "ghost sm icon" } = {}) {
    const wrap = h("span", { class: "so-menu-wrap" });
    const list = h("div", { class: "so-menu", role: "menu", hidden: true });
    const button = h("button", { type: "button", class: `btn ${kind}`, "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": label, title: label }, Aira.icon(icon));
    const close = () => { list.hidden = true; button.setAttribute("aria-expanded", "false"); document.removeEventListener("click", outside, true); };
    const outside = (event) => { if (!wrap.contains(event.target)) close(); };
    const open = () => {
      list.replaceChildren(...items.filter(Boolean).map((item) => h("button", { type: "button", role: "menuitem", class: `so-menu-item ${item.danger ? "danger" : ""}`.trim(), onclick: async (event) => { close(); await Aira.withBusy(event.currentTarget, item.run); } }, item.icon ? Aira.icon(item.icon) : null, h("span", { text: item.label }))));
      list.hidden = false;
      button.setAttribute("aria-expanded", "true");
      document.addEventListener("click", outside, true);
      const first = list.querySelector("button");
      if (first) first.focus();
    };
    button.addEventListener("click", () => (list.hidden ? open() : close()));
    list.addEventListener("keydown", (event) => {
      const all = [...list.querySelectorAll("button")];
      const at = all.indexOf(document.activeElement);
      if (event.key === "Escape") { event.stopPropagation(); close(); button.focus(); }
      else if (event.key === "ArrowDown") { event.preventDefault(); all[(at + 1) % all.length].focus(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); all[(at - 1 + all.length) % all.length].focus(); }
    });
    wrap.append(button, list);
    return wrap;
  }

  // ---------------------------------------------------------------------------
  // Photos from the device: shrunk in the browser (which leaves the photo's metadata behind), then uploaded
  // ---------------------------------------------------------------------------
  const MAX_SIDE = 2048;
  const asData = (blob) => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error("This photo could not be read.")); reader.readAsDataURL(blob); });
  async function shrink(file) {
    let bitmap = null;
    try { bitmap = await createImageBitmap(file, { imageOrientation: "from-image" }); } catch { bitmap = null; }
    if (!bitmap) {
      if (/^image\/(jpeg|png|webp)$/.test(file.type) && file.size <= 8 * 1024 * 1024) return file;
      throw new Error("Choose a JPEG, PNG or WebP photo.");
    }
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    const png = file.type === "image/png" && file.size < 3 * 1024 * 1024;
    if (!png) { context.fillStyle = "#ffffff"; context.fillRect(0, 0, canvas.width, canvas.height); }
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    if (bitmap.close) bitmap.close();
    return new Promise((resolve, reject) => canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("This photo could not be prepared."))), png ? "image/png" : "image/jpeg", 0.86));
  }
  async function uploadPhoto(file) {
    if (!file || !/^image\//.test(file.type || "image/")) throw new Error("Choose a photo (JPEG, PNG or WebP).");
    const data = await asData(await shrink(file));
    return (await api("/photos", { method: "POST", body: { data } })).photo;
  }
  // Ask for photos with the system's file chooser (or the camera on a phone).
  const choosePhotos = (multiple = false) => new Promise((resolve) => {
    const input = h("input", { type: "file", accept: "image/*", multiple, hidden: true, "aria-hidden": "true" });
    input.addEventListener("change", () => { resolve([...(input.files || [])]); input.remove(); });
    document.body.append(input);
    input.click();
  });
  // The photos of a form: "Add photos" opens the file chooser; each shows as a thumbnail with its description
  // (alt text) and a remove button while the others upload. items() -> [{ ref, url, alt }]
  function photoPicker({ max = 6, label = "Add photos", existing = [], withAlt = true, onChange = () => {} } = {}) {
    const items = existing.map((url) => ({ ref: url, url, alt: "" }));
    let busy = 0;
    const strip = h("div", { class: "so-thumbs", role: "list", "aria-label": "Photos to share" });
    const input = h("input", { type: "file", accept: "image/*", multiple: max > 1, class: "so-file", "aria-label": label, "data-so-photo-input": "" });
    const status = h("span", { class: "muted small", role: "status", "aria-live": "polite" });
    const button = h("label", { class: "btn ghost sm so-file-label" }, Aira.icon("soPhoto"), h("span", { text: label }), input);
    const draw = () => {
      strip.replaceChildren(...items.map((item, index) => h("div", { class: "so-thumb", role: "listitem" },
        h("img", { src: item.url, alt: item.alt || `Photo ${index + 1}` }),
        h("button", { type: "button", class: "so-thumb-x", "aria-label": `Remove photo ${index + 1}`, text: "✕", onclick: () => { items.splice(index, 1); draw(); onChange(); } }),
        withAlt ? h("input", { type: "text", class: "so-thumb-alt", maxlength: "200", placeholder: "Describe it", "aria-label": `Description of photo ${index + 1} for people who cannot see it`, value: item.alt, oninput: (event) => { item.alt = event.target.value; } }) : null)));
      strip.hidden = !items.length;
      button.hidden = items.length >= max;
      status.textContent = busy ? `Uploading ${busy === 1 ? "a photo" : `${busy} photos`}…` : "";
    };
    const add = async (files) => {
      const room = Math.max(0, max - items.length - busy);
      if (files.length > room) Aira.toast(`You can add ${max === 1 ? "one photo" : `up to ${max} photos`}.`, "info");
      for (const file of files.slice(0, room)) {
        busy += 1; draw();
        try { const photo = await uploadPhoto(file); items.push({ ref: photo.ref, url: photo.url, alt: "" }); } catch (error) { Aira.toast(error.message, "error"); }
        busy -= 1; draw(); onChange();
      }
    };
    input.addEventListener("change", async () => { const files = [...(input.files || [])]; input.value = ""; await add(files); });
    draw();
    return { node: h("div", { class: "so-picker" }, strip, h("div", { class: "so-picker-row" }, button, status)), items: () => items, busy: () => busy > 0, clear: () => { items.splice(0); draw(); }, add, button };
  }
  // One photo for a picture (profile, cover, group, page): choose, upload, then save(ref).
  async function changePicture(save) {
    const [file] = await choosePhotos(false);
    if (!file) return;
    Aira.toast("Uploading the photo…", "info");
    const photo = await uploadPhoto(file);
    await save(photo.ref);
  }

  // Photos of a post: one photo, or several in a carousel you swipe (or move with the arrows); a filter by name.
  function carousel(photos, { alts = [], filter = "", owner = "" } = {}) {
    const list = (photos || []).filter(isPicture);
    if (!list.length) return null;
    const label = (index) => alts[index] || `Photo ${index + 1} from ${owner}`;
    const open = (index) => {
      const big = h("img", { src: list[index], alt: label(index), class: `so-lightbox-img ${filter ? `so-filter-${filter}` : ""}`.trim(), referrerpolicy: "no-referrer" });
      Aira.modal({ title: owner ? `${owner}'s photo` : "Photo", body: h("div", { class: "so-lightbox" }, big, alts[index] ? h("p", { class: "muted small", text: alts[index] }) : null), actions: [{ label: "Close", kind: "secondary" }], wide: true });
    };
    const slide = (src, index) => h("button", { type: "button", class: "so-slide", "aria-label": `Open ${label(index)}`, onclick: () => open(index) },
      h("img", { src, alt: label(index), loading: "lazy", referrerpolicy: "no-referrer" }));
    if (list.length === 1) return h("div", { class: `so-media ${filter ? `so-filter-${filter}` : ""}`.trim() }, slide(list[0], 0));
    const track = h("div", { class: "so-track", tabindex: "0", "aria-label": `${list.length} photos. Use the arrow keys or swipe to see them all.` }, ...list.map(slide));
    const dots = h("div", { class: "so-dots", "aria-hidden": "true" }, ...list.map((_, index) => h("span", { class: index === 0 ? "on" : "" })));
    const counter = h("span", { class: "so-counter", "aria-live": "polite", text: `1/${list.length}` });
    const at = () => Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
    const show = (index) => track.scrollTo({ left: Math.max(0, Math.min(list.length - 1, index)) * track.clientWidth, behavior: "smooth" });
    track.addEventListener("scroll", () => { const index = at(); counter.textContent = `${index + 1}/${list.length}`; [...dots.children].forEach((dot, n) => dot.classList.toggle("on", n === index)); }, { passive: true });
    track.addEventListener("keydown", (event) => { if (event.key === "ArrowRight") { event.preventDefault(); show(at() + 1); } if (event.key === "ArrowLeft") { event.preventDefault(); show(at() - 1); } });
    return h("div", { class: `so-media so-carousel ${filter ? `so-filter-${filter}` : ""}`.trim() }, track,
      h("button", { type: "button", class: "so-nav so-prev", "aria-label": "Previous photo", onclick: () => show(at() - 1) }, Aira.icon("soLeft")),
      h("button", { type: "button", class: "so-nav so-next", "aria-label": "Next photo", onclick: () => show(at() + 1) }, Aira.icon("soRight")),
      counter, dots);
  }
  const photoGrid = (photos, owner) => carousel(photos, { owner });

  // ---------------------------------------------------------------------------
  // Posts
  // ---------------------------------------------------------------------------
  // Save a post into a collection (or All saved), make a new collection, or take it out of Saved.
  async function saveTo(post, after) {
    const data = await api("/collections");
    const pickList = h("div", { class: "so-pick-list", role: "group", "aria-label": "Collections" });
    const name = h("input", { type: "text", maxlength: "60", placeholder: "New collection, e.g. Recipes", "aria-label": "Name of a new collection" });
    let dialog = null;
    const save = async (collectionId) => {
      const result = await api(`/posts/${post.id}/save`, { method: "POST", body: { collection_id: collectionId } });
      Object.assign(post, { saved: result.saved, collection_id: result.collection_id });
      const where = data.collections.find((item) => item.id === collectionId);
      Aira.toast(`Saved to ${where ? where.name : "All saved"}.`, "success");
      if (dialog) dialog.close();
      await after();
    };
    pickList.append(...data.collections.map((item) => h("button", { type: "button", class: `so-pick ${post.saved && post.collection_id === item.id ? "on" : ""}`.trim(), "aria-pressed": String(!!post.saved && post.collection_id === item.id), onclick: (event) => Aira.withBusy(event.currentTarget, () => save(item.id)) },
      item.cover ? h("img", { src: item.cover, alt: "" }) : h("span", { class: "so-pick-icon", "aria-hidden": "true" }, Aira.icon("soBookmark")),
      h("span", {}, h("strong", { text: item.name }), h("span", { class: "muted small", text: count(item.count, "post", "posts") })))));
    const create = async () => {
      const wanted = name.value.trim();
      if (!wanted) { name.focus(); return; }
      const made = (await api("/collections", { method: "POST", body: { name: wanted } })).collection;
      data.collections.push(made);
      await save(made.id);
    };
    name.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); create(); } });
    dialog = Aira.modal({
      title: "Save to a collection",
      body: h("div", { class: "suite-body" }, pickList, h("div", { class: "so-inline" }, h("div", { class: "so-grow" }, name), S.button("Create and save", create, { kind: "secondary sm" }))),
      actions: [post.saved ? { label: "Remove from Saved", kind: "ghost", onClick: async () => { await api(`/posts/${post.id}/save`, { method: "POST", body: { saved: false } }); Object.assign(post, { saved: false, collection_id: "" }); Aira.toast("Removed from Saved.", "success"); await after(); } } : null, { label: "Close", kind: "secondary" }].filter(Boolean),
    });
  }

  // A poll: buttons to vote while it is open; bars with the results once you voted, when it is over, or for its author.
  function pollNode(post, ctx) {
    const poll = post.poll;
    const left = () => {
      const ms = new Date(poll.closes_at).getTime() - Date.now();
      if (poll.closed || ms <= 0) return "Final results";
      const hours = Math.round(ms / 3600000);
      return hours >= 24 ? count(Math.round(hours / 24), "day left", "days left") : hours >= 1 ? count(hours, "hour left", "hours left") : "Closing soon";
    };
    const vote = async (index) => { const result = await api(`/posts/${post.id}/vote`, { method: "POST", body: { choice: index } }); Object.assign(post, result.post); ctx.redraw(); };
    const options = poll.options.map((option, index) => (poll.results
      ? h("div", { class: `so-poll-bar ${poll.my_vote === index ? "mine" : ""}`.trim() },
        h("span", { class: "so-poll-fill", "aria-hidden": "true", style: { width: `${option.percent || 0}%` } }),
        h("span", { class: "so-poll-label", text: `${poll.my_vote === index ? "✓ " : ""}${option.text}` }),
        h("strong", { class: "so-poll-pct", text: `${option.percent || 0}%` }))
      : h("button", { type: "button", class: "so-poll-choice", text: option.text, onclick: (event) => Aira.withBusy(event.currentTarget, () => vote(index)) })));
    return h("div", { class: "so-poll", role: "group", "aria-label": "Poll" }, ...options,
      h("p", { class: "muted small", style: { margin: 0 }, text: `${count(poll.total, "vote", "votes")} · ${left()}${poll.results ? "" : " · Vote to see the results"}` }));
  }

  // Comments with one level of replies: Like and Reply under each; a reply box opens under the thread.
  function commentsBlock(post, ctx, box) {
    const load = async () => {
      const data = await api(`/posts/${post.id}/comments`);
      const input = h("input", { type: "text", placeholder: "Write a comment", "aria-label": `Comment on ${post.author.name}'s post`, maxlength: "1000", "data-comment-for": post.id, value: ctx.drafts.get(post.id) || "" });
      input.addEventListener("input", () => { if (input.value) ctx.drafts.set(post.id, input.value); else ctx.drafts.delete(post.id); });
      const send = async () => { if (!input.value.trim()) return; await api(`/posts/${post.id}/comments`, { method: "POST", body: { text: input.value } }); ctx.drafts.delete(post.id); post.comments += 1; await load(); const again = box.querySelector(`[data-comment-for="${post.id}"]`); if (again) again.focus(); };
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); send(); } });
      if (ctx.refocus === post.id) { ctx.refocus = ""; setTimeout(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }, 0); }
      const tops = data.comments.filter((item) => !item.parent_id);
      const replies = (id) => data.comments.filter((item) => item.parent_id === id);
      const one = (item, thread) => {
        const like = h("button", { type: "button", class: `so-link-btn ${item.liked ? "on" : ""}`.trim(), "aria-pressed": String(item.liked), "aria-label": `Like ${item.author.name}'s comment${item.likes ? ` (${item.likes})` : ""}`, text: item.likes ? `Like · ${item.likes}` : "Like", onclick: async () => { const result = await api(`/comments/${item.id}/like`, { method: "POST", body: { liked: !item.liked } }); item.liked = result.liked; item.likes = result.likes; like.textContent = item.likes ? `Like · ${item.likes}` : "Like"; like.classList.toggle("on", item.liked); like.setAttribute("aria-pressed", String(item.liked)); } });
        const reply = h("button", { type: "button", class: "so-link-btn", text: "Reply", "aria-label": `Reply to ${item.author.name}`, onclick: () => openReply(thread, item) });
        return h("li", { class: "so-comment", dataset: { comment: item.id } }, face(item.author, item.parent_id ? 26 : 32),
          h("div", { class: "so-comment-main" },
            h("div", { class: "so-bubble" }, personLink(item.author), h("span", { class: "so-bubble-text", text: item.text })),
            h("div", { class: "so-comment-meta" }, h("span", { class: "muted small", text: when(item.created_at) }), like, reply,
              item.can_delete ? h("button", { type: "button", class: "so-link-btn", text: "Delete", "aria-label": "Delete comment", onclick: async () => { await api(`/comments/${item.id}`, { method: "DELETE" }); post.comments = Math.max(0, post.comments - 1 - replies(item.id).length); await load(); } }) : null,
              item.mine ? null : h("button", { type: "button", class: "so-link-btn", text: "Report", "aria-label": "Report comment", onclick: () => report("comment", item.id) }))));
      };
      const openReply = (thread, item) => {
        const existing = thread.querySelector(".so-reply-box");
        if (existing) existing.remove();
        const field = h("input", { type: "text", maxlength: "1000", placeholder: `Reply to ${item.author.name}`, "aria-label": `Your reply to ${item.author.name}`, value: item.parent_id ? `@${item.author.handle} ` : "" });
        const sendReply = async () => { if (!field.value.trim()) return; await api(`/posts/${post.id}/comments`, { method: "POST", body: { text: field.value, parent_id: item.id } }); post.comments += 1; await load(); };
        field.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); sendReply(); } if (event.key === "Escape") { event.stopPropagation(); row.remove(); } });
        const row = h("div", { class: "so-reply-box" }, face(ctx.me, 26), h("div", { class: "so-grow" }, field), S.button("Reply", sendReply, { kind: "secondary sm" }));
        thread.append(row);
        field.focus();
      };
      box.replaceChildren(
        tops.length ? h("ul", { class: "so-comments" }, ...tops.map((item) => {
          const thread = h("div", { class: "so-thread" });
          const nested = replies(item.id);
          if (nested.length) thread.append(h("ul", { class: "so-comments so-replies" }, ...nested.map((reply) => one(reply, thread))));
          const node = one(item, thread);
          node.querySelector(".so-comment-main").append(thread);
          return node;
        })) : h("p", { class: "muted small", text: "No comments yet. Be the first." }),
        h("div", { class: "so-comment-new" }, face(ctx.me, 32), h("div", { class: "so-grow" }, input), S.button("Comment", send, { kind: "secondary sm" })));
    };
    return load;
  }

  function postNode(post, ctx) {
    const reactionRow = h("div", { class: "so-reactions", role: "group", "aria-label": "Reactions" }, ...REACTIONS.map((emoji) => {
      const n = post.reactions[emoji] || 0;
      const mine = post.my_reaction === emoji;
      return h("button", { type: "button", class: `so-react ${mine ? "on" : ""}`.trim(), "aria-pressed": String(mine), "aria-label": `${REACTION_NAMES[emoji]}${n ? ` (${n})` : ""}`, title: REACTION_NAMES[emoji], onclick: async () => { const result = await api(`/posts/${post.id}/react`, { method: "POST", body: { emoji: mine ? null : emoji } }); Object.assign(post, result.post); ctx.redraw(); } }, h("span", { text: emoji }), n ? h("span", { class: "so-react-n", text: String(n) }) : null);
    }));
    // Open comments and half-typed comments survive the live feed redrawing this post.
    const comments = h("div", { hidden: !(ctx.open || ctx.openIds.has(post.id)), class: "so-comments-box" });
    const loadComments = commentsBlock(post, ctx, comments);
    if (!comments.hidden) loadComments();
    const share = async () => {
      await S.edit({ title: "Share this post", fields: [{ name: "text", label: "Say something about it (optional)", type: "textarea", rows: 3 }], submitLabel: "Share", onSave: async (values) => { await api("/posts", { method: "POST", body: { shared_post_id: post.id, text: values.text, audience: ctx.young ? "friends" : "public" } }); Aira.toast("Shared on your profile.", "success"); await ctx.reload(); } });
    };
    const feeling = FEELINGS[post.feeling];
    const where = post.group ? h("a", { href: `#/social?tab=groups&group=${post.group.id}`, text: ` in ${post.group.name}` }) : post.page ? h("a", { href: `#/social?tab=pages&page=${post.page.id}`, text: ` · ${post.page.name}` }) : null;
    const shared = post.shared ? (post.shared.gone ? h("p", { class: "muted", text: "This post is no longer available." }) : h("div", { class: "so-shared" }, h("div", { class: "so-inline" }, face(post.shared.author, 28), personLink(post.shared.author), h("span", { class: "muted small", text: when(post.shared.created_at) })), post.shared.text ? richText(post.shared.text) : null, carousel(post.shared.photos, { alts: post.shared.alts || [], filter: post.shared.filter || "", owner: post.shared.author.name }))) : null;
    const pin = async (on) => { const result = await api(`/posts/${post.id}/pin`, { method: "POST", body: { pinned: on } }); Object.assign(post, result.post); Aira.toast(on ? "Pinned to the top of your profile." : "Unpinned.", "success"); ctx.redraw(); };
    const ownProfilePost = post.mine && !post.group && !post.page;
    const menu = menuButton("More options for this post", [
      post.mine ? { label: "Edit", icon: "soPen", run: () => S.edit({ title: "Edit your post", fields: [{ name: "text", label: "Your post", type: "textarea", rows: 5 }, { name: "place", label: "Where (optional)" }], values: post, onSave: async (values) => { const result = await api(`/posts/${post.id}`, { method: "PATCH", body: { text: values.text, place: values.place } }); Object.assign(post, result.post); ctx.redraw(); } }) } : null,
      ownProfilePost ? { label: post.pinned ? "Unpin from your profile" : "Pin to your profile", icon: "soPin", run: () => pin(!post.pinned) } : null,
      { label: post.saved ? "Saved: change collection" : "Save to a collection", icon: "soBookmark", run: () => saveTo(post, async () => ctx.redraw()) },
      post.mine ? { label: "Delete", danger: true, run: async () => { if (await S.confirmDelete("this post")) { await api(`/posts/${post.id}`, { method: "DELETE" }); ctx.remove(post.id); } } } : null,
      post.mine ? null : { label: "Report", icon: "soFlag", run: () => report("post", post.id) },
    ]);
    const total = Object.values(post.reactions).reduce((sum, n) => sum + n, 0);
    const top = Object.entries(post.reactions).filter(([, n]) => n).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([emoji]) => emoji).join("");
    return h("article", { class: "card so-post", "aria-label": `Post by ${post.author.name}` },
      h("header", { class: "so-post-head" }, face(post.author, 44),
        h("div", { class: "so-post-who" },
          h("div", { class: "so-post-line" }, personLink(post.author), feeling ? h("span", { class: "muted", text: ` is ${feeling[0]} ${feeling[1]}` }) : null, post.place ? h("span", { class: "muted", text: ` at 📍 ${post.place}` }) : null, where),
          h("div", { class: "muted small" }, h("span", { text: when(post.created_at) }), h("span", { text: " · " }), h("span", { title: AUDIENCE_LABELS[post.audience] || "", "aria-label": AUDIENCE_LABELS[post.audience] || "", text: `${AUDIENCE_ICONS[post.audience] || ""} ${AUDIENCE_LABELS[post.audience] || ""}` }), post.edited ? h("span", { text: " · edited" }) : null)),
        post.pinned ? h("span", { class: "so-chip", text: "📌 Pinned" }) : null,
        menu),
      post.text ? richText(post.text) : null,
      carousel(post.photos, { alts: post.alts || [], filter: post.filter || "", owner: post.author.name }),
      post.poll ? pollNode(post, ctx) : null,
      shared,
      total || post.comments ? h("div", { class: "so-post-stats muted small" }, total ? h("span", { text: `${top} ${total}` }) : h("span"), post.comments ? h("span", { text: count(post.comments, "comment", "comments") }) : null) : null,
      reactionRow,
      h("div", { class: "so-post-actions" },
        h("button", { type: "button", class: "so-action", "aria-expanded": String(!comments.hidden), onclick: async (event) => { comments.hidden = !comments.hidden; event.currentTarget.setAttribute("aria-expanded", String(!comments.hidden)); if (comments.hidden) ctx.openIds.delete(post.id); else { ctx.openIds.add(post.id); await loadComments(); } } }, Aira.icon("soChat"), h("span", { text: "Comment" })),
        post.can_share ? h("button", { type: "button", class: "so-action", onclick: share }, Aira.icon("soShare"), h("span", { text: "Share" })) : null,
        h("button", { type: "button", class: `so-action ${post.saved ? "on" : ""}`.trim(), "aria-pressed": String(!!post.saved), onclick: () => saveTo(post, async () => ctx.redraw()) }, Aira.icon("soBookmark"), h("span", { text: post.saved ? "Saved" : "Save" }))),
      comments);
  }

  // A list of posts that redraws in place; ``load`` returns the posts. Only posts that changed are rebuilt, and
  // nodes that stay are not moved, so a comment being typed keeps its text and focus while the feed updates.
  function postList(container, me, load, { open = false, empty } = {}) {
    const state = { posts: [] };
    const list = h("div", { class: "so-posts" });
    const built = new Map();
    const emptyNode = empty || Aira.ui.empty("Nothing here yet", "Posts appear here.");
    const ctx = {
      me, open, young: young(me), openIds: new Set(), drafts: new Map(), refocus: "",
      redraw: () => {
        const active = document.activeElement;
        const typing = active && active.getAttribute ? active.getAttribute("data-comment-for") : "";
        const wanted = state.posts.length ? state.posts.map((post) => {
          const key = JSON.stringify(post);
          const known = built.get(post.id);
          if (known && known.key === key) return known.node;
          if (typing === post.id) ctx.refocus = post.id;
          const node = postNode(post, ctx);
          built.set(post.id, { key, node });
          return node;
        }) : [emptyNode];
        for (const id of [...built.keys()]) if (!state.posts.some((post) => post.id === id)) built.delete(id);
        wanted.forEach((node, index) => { if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null); });
        while (list.children.length > wanted.length) list.lastChild.remove();
      },
      reload: async () => { state.posts = await load(); built.clear(); ctx.redraw(); },
      remove: (id) => { state.posts = state.posts.filter((post) => post.id !== id); ctx.redraw(); },
    };
    container.append(list);
    return { state, ctx };
  }

  // The composer: words, photos from the device (with a description each and a filter), a poll, a feeling or
  // activity, a place and who can see it, then Post.
  function composer(me, { groupId = "", pageId = "", placeholder, onPosted }) {
    const isYoung = young(me);
    const text = h("textarea", { rows: "2", placeholder: placeholder || `What's on your mind, ${me.display_name}?`, "aria-label": "Your post", maxlength: "2000", "data-so-composer": "" });
    text.addEventListener("input", () => { text.rows = Math.min(8, Math.max(2, text.value.split("\n").length + 1)); });
    const audience = Aira.ui.select(isYoung ? [["friends", "👥 Friends"], ["close_friends", "⭐ Close friends"], ["only_me", "🔒 Only me"]] : [["friends", "👥 Friends"], ["public", "🌍 Everyone"], ["close_friends", "⭐ Close friends"], ["only_me", "🔒 Only me"]], "friends", { "aria-label": "Who can see it" });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    let filter = "";
    const filterRow = h("div", { class: "so-filters-row", role: "radiogroup", "aria-label": "Photo filter", hidden: true });
    const picker = photoPicker({ onChange: () => drawFilters() });
    const drawFilters = () => {
      const first = picker.items()[0];
      filterRow.hidden = !first;
      if (!first) return;
      filterRow.replaceChildren(...FILTERS.map(([id, label]) => h("button", { type: "button", role: "radio", "aria-checked": String(filter === id), class: `so-filter-chip ${filter === id ? "on" : ""}`.trim(), onclick: () => { filter = id; drawFilters(); } },
        h("span", { class: `so-filter-swatch ${id ? `so-filter-${id}` : ""}`.trim() }, h("img", { src: first.url, alt: "" })), h("span", { class: "small", text: label }))));
    };
    picker.node.hidden = true;
    // a poll: two to four answers and how long it runs
    const pollBox = h("div", { class: "so-poll-edit", hidden: true });
    const answers = [];
    const days = Aira.ui.select([["1", "1 day"], ["3", "3 days"], ["7", "1 week"]], "1", { "aria-label": "How long the poll runs" });
    const addAnswer = () => {
      if (answers.length >= 4) return;
      const field = h("input", { type: "text", maxlength: "80", placeholder: `Answer ${answers.length + 1}`, "aria-label": `Poll answer ${answers.length + 1}` });
      answers.push(field);
      drawPoll();
      return field;
    };
    const drawPoll = () => pollBox.replaceChildren(h("strong", { class: "small", text: "Poll: your post is the question" }), ...answers, h("div", { class: "so-inline" }, answers.length < 4 ? S.button("Add an answer", () => { const field = addAnswer(); if (field) field.focus(); }, { kind: "ghost sm", icon: "soPlus" }) : null, h("span", { class: "so-grow" }), h("label", { class: "so-inline small" }, h("span", { text: "Runs for" }), days), S.button("Remove poll", () => { answers.splice(0); pollBox.hidden = true; }, { kind: "ghost sm" })));
    const feeling = Aira.ui.select([["", "Feeling or activity…"], ...Object.entries(FEELINGS).map(([id, [emoji, label]]) => [id, `${emoji} ${label}`])], "", { "aria-label": "Feeling or activity" });
    const place = h("input", { type: "text", maxlength: "80", placeholder: "Where are you?", "aria-label": "Place" });
    const extras = h("div", { class: "so-composer-extras", hidden: true }, feeling, place);
    const tool = (icon, label, run) => h("button", { type: "button", class: "so-tool", title: label, "aria-label": label, onclick: run }, Aira.icon(icon), h("span", { class: "so-tool-label", text: label }));
    const reset = () => {
      text.value = ""; text.rows = 2; place.value = ""; feeling.value = ""; filter = "";
      picker.clear(); picker.node.hidden = true; answers.splice(0); pollBox.hidden = true; extras.hidden = true; drawFilters();
    };
    const post = async () => {
      error.hidden = true;
      if (picker.busy()) { error.textContent = "Wait for the photos to finish uploading."; error.hidden = false; return; }
      const items = picker.items();
      const options = answers.map((field) => field.value.trim()).filter(Boolean);
      try {
        await api("/posts", { method: "POST", body: {
          text: text.value, photos: items.map((item) => item.ref), alts: items.map((item) => item.alt || ""), filter: items.length ? filter : "",
          audience: audience.value, group_id: groupId, page_id: pageId, feeling: feeling.value, place: place.value,
          poll: pollBox.hidden ? null : { options, days: Number(days.value) },
        } });
        reset();
        Aira.toast("Posted.", "success");
        await onPosted();
      } catch (err) { error.textContent = err.message; error.hidden = false; }
    };
    const openPhotos = () => { picker.node.hidden = false; picker.button.querySelector("input").click(); };
    const openPoll = () => { if (pollBox.hidden) { pollBox.hidden = false; if (!answers.length) { addAnswer(); addAnswer(); } drawPoll(); } answers[0].focus(); if (!text.value) text.placeholder = "Ask a question…"; };
    const who = groupId || pageId ? null : h("label", { class: "so-audience" }, h("span", { class: "sr-only", text: "Who can see it" }), audience);
    const node = h("section", { class: "card so-composer" },
      h("div", { class: "so-composer-row" }, face(me, 44), h("div", { class: "so-composer-text" }, text)),
      picker.node, filterRow, pollBox, extras, error,
      h("div", { class: "so-composer-actions" },
        h("div", { class: "so-tools", role: "group", "aria-label": "Add to your post" },
          tool("soPhoto", "Photo", openPhotos),
          tool("soPoll", "Poll", openPoll),
          tool("soSmile", "Feeling", () => { extras.hidden = false; feeling.focus(); }),
          tool("soPin", "Place", () => { extras.hidden = false; place.focus(); })),
        h("span", { class: "so-grow" }), who, S.button("Post", post, { kind: "primary" })),
      isYoung && !groupId && !pageId ? h("p", { class: "muted small", style: { margin: 0 }, text: "Young people share with friends only." }) : null);
    node.openPhotos = openPhotos;
    node.openPoll = openPoll;
    return node;
  }

  // ---------------------------------------------------------------------------
  // Stories: a full-size viewer with progress bars, replies and quick reactions; highlights keep some for good
  // ---------------------------------------------------------------------------
  const STORY_MS = 6000;
  function showStories(person, onDone, { title } = {}) {
    let index = Math.max(0, person.stories.findIndex((story) => !story.seen));
    let timer = 0;
    let paused = false;
    const bars = h("div", { class: "so-story-bars", "aria-hidden": "true" }, ...person.stories.map(() => h("span", {}, h("i"))));
    const stage = h("div", { class: "so-story-stage" });
    const foot = h("div", { class: "so-story-foot" });
    const meta = h("div", { class: "so-story-meta" });
    const view = h("div", { class: "so-story-view" }, bars, meta, stage, foot);
    const stop = () => { clearTimeout(timer); timer = 0; };
    const schedule = () => { stop(); if (!paused && document.body.contains(view)) timer = setTimeout(() => (index < person.stories.length - 1 ? move(1) : dialog.close()), STORY_MS); };
    const move = (step) => { index = Math.max(0, Math.min(person.stories.length - 1, index + step)); draw(); };
    const pause = (on) => { paused = on; view.classList.toggle("so-paused", on); if (on) stop(); else schedule(); };
    const draw = () => {
      const story = person.stories[index];
      if (!person.mine) api(`/stories/${story.id}/seen`, { method: "POST", body: {}, silent: true }).catch(() => {});
      story.seen = true;
      [...bars.children].forEach((bar, n) => { bar.className = n < index ? "done" : n === index ? "now" : ""; });
      meta.replaceChildren(...kids(face(person.author, 32), h("strong", { text: person.author.name }), h("span", { class: "small", text: when(story.created_at) }), story.audience === "close_friends" ? h("span", { class: "so-chip so-chip-star", text: "⭐ Close friends" }) : null));
      stage.replaceChildren(...kids(
        h("div", { class: "so-story-card", style: { background: story.color } },
          story.photo ? h("img", { src: story.photo, alt: `Story photo from ${person.author.name}`, referrerpolicy: "no-referrer" }) : null,
          story.text ? h("div", { class: "so-story-words", text: story.text }) : null),
        index > 0 ? h("button", { type: "button", class: "so-story-tap so-left", "aria-label": "Previous story", onclick: () => move(-1) }, Aira.icon("soLeft")) : null,
        index < person.stories.length - 1 ? h("button", { type: "button", class: "so-story-tap so-right", "aria-label": "Next story", onclick: () => move(1) }, Aira.icon("soRight")) : null));
      if (person.mine) {
        foot.replaceChildren(h("div", { class: "so-inline" },
          story.views !== null ? S.button(`Seen by ${count(story.views, "person", "people")}${story.reactions ? ` · ${story.reactions} ♥` : ""}`, async () => {
            pause(true);
            const data = await api(`/stories/${story.id}/viewers`);
            Aira.modal({ title: "Who saw your story", body: data.viewers.length ? h("ul", { class: "life-people" }, ...data.viewers.map((item) => personRow(item, item.reaction ? h("span", { class: "so-big-emoji", text: item.reaction }) : null, h("span", { class: "muted small", text: when(item.at) })))) : Aira.ui.empty("Nobody yet", "Your friends' names appear here when they see it."), actions: [{ label: "Close", kind: "secondary" }], onClose: () => pause(false) });
          }, { kind: "ghost sm" }) : null,
          S.button("Add to a highlight", () => { pause(true); addHighlight([story.id], () => pause(false)); }, { kind: "ghost sm", icon: "soStar" }),
          S.button("Delete this story", async () => { await api(`/stories/${story.id}`, { method: "DELETE" }); dialog.close(); }, { kind: "ghost sm" })));
      } else {
        const reply = h("input", { type: "text", maxlength: "1000", placeholder: `Reply to ${person.author.name}…`, "aria-label": `Reply to ${person.author.name}'s story`, "data-story-reply": "" });
        reply.addEventListener("focus", () => pause(true));
        reply.addEventListener("blur", () => { if (!reply.value) pause(false); });
        const sendReply = async () => {
          if (!reply.value.trim()) { reply.focus(); return; }
          await api(`/stories/${story.id}/reply`, { method: "POST", body: { text: reply.value } });
          reply.value = "";
          Aira.toast(`Sent to ${person.author.name} in Chats.`, "success");
          pause(false);
        };
        reply.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); sendReply(); } });
        foot.replaceChildren(
          h("div", { class: "so-story-react", role: "group", "aria-label": "Quick reactions" }, ...STORY_REACTIONS.map((emoji) => h("button", { type: "button", class: story.my_reaction === emoji ? "on" : "", "aria-pressed": String(story.my_reaction === emoji), "aria-label": `React ${emoji}`, text: emoji, onclick: async () => { const result = await api(`/stories/${story.id}/react`, { method: "POST", body: { emoji: story.my_reaction === emoji ? "" : emoji } }); story.my_reaction = result.my_reaction; draw(); if (result.my_reaction) Aira.toast(`You reacted ${emoji}.`, "success"); } }))),
          h("div", { class: "so-story-reply" }, h("div", { class: "so-grow" }, reply), S.button("Send", sendReply, { kind: "secondary sm", icon: "soSend" }), S.button("Report", () => { pause(true); report("story", story.id); }, { kind: "ghost sm" })));
      }
      schedule();
    };
    view.addEventListener("keydown", (event) => { if (event.target.tagName === "INPUT") return; if (event.key === "ArrowRight") move(1); if (event.key === "ArrowLeft") move(-1); });
    view.addEventListener("pointerdown", (event) => { if (event.target.closest(".so-story-card")) pause(true); });
    view.addEventListener("pointerup", (event) => { if (event.target.closest(".so-story-card") && !view.querySelector("input:focus")) pause(false); });
    const dialog = Aira.modal({ title: title || `${person.author.name}'s story`, body: view, actions: [{ label: "Close", kind: "secondary" }], onClose: () => { stop(); if (onDone) onDone(); } });
    dialog.box.classList.add("so-story-modal");
    draw();
  }

  // Add a story: a photo from the device (or words on a colour), for friends or close friends only.
  function addStory(onDone) {
    const picker = photoPicker({ max: 1, label: "Add a photo", withAlt: false });
    const text = h("textarea", { rows: "3", maxlength: "200", placeholder: "Say something", "aria-label": "Text" });
    const colors = ["#2563eb", "#db2777", "#16a34a", "#ea580c", "#7c3aed", "#0f172a"];
    let color = colors[0];
    const preview = h("div", { class: "so-story-preview" });
    const swatches = h("div", { class: "so-swatches", role: "radiogroup", "aria-label": "Background colour" });
    const audience = Aira.ui.select([["friends", "👥 My friends"], ["close_friends", "⭐ Close friends only"]], "friends", { "aria-label": "Who can see it" });
    const draw = () => {
      const photo = picker.items()[0];
      preview.style.background = color;
      preview.replaceChildren(...kids(photo ? h("img", { src: photo.url, alt: "" }) : null, h("span", { text: text.value || (photo ? "" : "Your story") })));
      swatches.replaceChildren(...colors.map((value) => h("button", { type: "button", role: "radio", "aria-checked": String(value === color), "aria-label": `Colour ${value}`, class: value === color ? "on" : "", style: { background: value }, onclick: () => { color = value; draw(); } })));
    };
    text.addEventListener("input", draw);
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const body = h("div", { class: "so-story-new" }, preview, h("div", { class: "suite-body" }, picker.node, Aira.ui.field("Text", text), h("div", { class: "field" }, h("span", { class: "small", text: "Background colour" }), swatches), Aira.ui.field("Who can see it", audience), h("p", { class: "muted small", style: { margin: 0 }, text: "Stories last 24 hours. Keep the best on your profile with a highlight." }), error));
    const observer = new MutationObserver(draw);
    observer.observe(picker.node, { childList: true, subtree: true });
    draw();
    return new Promise((resolve) => {
      Aira.modal({
        title: "Add a story (24 hours)", body,
        onClose: () => { observer.disconnect(); resolve(null); },
        actions: [{ label: "Cancel", kind: "ghost" }, { label: "Share the story", kind: "primary", onClick: async () => {
          error.hidden = true;
          if (picker.busy()) { error.textContent = "Wait for the photo to finish uploading."; error.hidden = false; return false; }
          const photo = picker.items()[0];
          try { await api("/stories", { method: "POST", body: { text: text.value, photo: photo ? photo.ref : "", color, audience: audience.value } }); } catch (err) { error.textContent = err.message; error.hidden = false; return false; }
          Aira.toast("Your story is up for 24 hours.", "success");
          await onDone();
          return true;
        } }],
      });
    });
  }

  // A highlight keeps chosen stories on your profile after their day is over.
  async function addHighlight(chosen = [], onDone = () => {}) {
    const [mine, existing] = await Promise.all([api("/stories/mine"), api(`/people/${known.id}`)]);
    if (!mine.stories.length) { Aira.toast("Share a story first: highlights keep your stories on your profile.", "info"); onDone(); return; }
    const picked = new Set(chosen);
    const title = h("input", { type: "text", maxlength: "30", placeholder: "Summer, Family, Food", "aria-label": "Highlight name" });
    const target = Aira.ui.select([["", "A new highlight"], ...existing.highlights.map((item) => [item.id, item.title])], "", { "aria-label": "Highlight" });
    const grid = h("div", { class: "so-story-pick", role: "group", "aria-label": "Your stories" }, ...mine.stories.map((story) => {
      const box = h("input", { type: "checkbox", checked: picked.has(story.id), "aria-label": `Story from ${new Date(story.created_at).toLocaleDateString()}${story.text ? `: ${story.text}` : ""}`, onchange: (event) => { if (event.target.checked) picked.add(story.id); else picked.delete(story.id); } });
      return h("label", { class: "so-story-pick-item", style: { background: story.color } }, story.photo ? h("img", { src: story.photo, alt: "" }) : h("span", { class: "small", text: story.text }), box);
    }));
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    target.addEventListener("change", () => { const found = existing.highlights.find((item) => item.id === target.value); title.value = found ? found.title : ""; if (found) for (const story of found.stories) picked.add(story.id); for (const box of grid.querySelectorAll("input")) box.checked = picked.has(mine.stories[[...grid.querySelectorAll("input")].indexOf(box)].id); });
    Aira.modal({
      title: "Keep stories in a highlight", body: h("div", { class: "suite-body" }, existing.highlights.length ? Aira.ui.field("Add to", target) : null, Aira.ui.field("Name", title), grid, error),
      onClose: onDone,
      actions: [{ label: "Cancel", kind: "ghost" }, { label: "Save highlight", kind: "primary", onClick: async () => {
        error.hidden = true;
        try {
          const body = { title: title.value, story_ids: [...picked] };
          if (target.value) await api(`/highlights/${target.value}`, { method: "PATCH", body }); else await api("/highlights", { method: "POST", body });
        } catch (err) { error.textContent = err.message; error.hidden = false; return false; }
        Aira.toast("Saved in your highlights on your profile.", "success");
        return true;
      } }],
    });
  }

  async function storiesStrip(me) {
    const strip = h("div", { role: "list", "aria-label": "Stories", class: "so-stories" });
    const draw = async () => {
      const data = await api("/stories", { silent: true });
      strip.replaceChildren(...kids(
        h("div", { role: "listitem" }, h("button", { type: "button", class: "so-story so-story-add", onclick: () => addStory(draw) },
          h("span", { class: "so-story-ring" }, face(me, 58), h("span", { class: "so-story-plus", "aria-hidden": "true" }, Aira.icon("plus"))),
          h("span", { class: "small", text: "Add a story" }))),
        ...data.people.map((person) => h("div", { role: "listitem" }, h("button", { type: "button", class: "so-story", "aria-label": `${person.author.name}'s story${person.unseen ? ` (${person.unseen} new)` : ""}`, onclick: () => showStories(person, draw) },
          h("span", { class: `so-story-ring ${person.unseen ? "so-unseen" : ""} ${person.close ? "so-close" : ""}`.trim() }, face(person.author, 58)),
          h("span", { class: "small", text: person.mine ? "Your story" : person.author.name })))),
        data.people.length ? null : h("p", { class: "muted small so-stories-hint", text: "Stories last a day: a photo or a few words for your friends." })));
    };
    strip.redraw = draw;
    await draw();
    return strip;
  }

  // ---------------------------------------------------------------------------
  // Home: stories, "On this day", the composer and the feed; on a wide screen a column with what to do next
  // ---------------------------------------------------------------------------
  const today = () => { const now = new Date(); return `${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`; };
  async function memoriesCard() {
    let data;
    try { data = await api(`/memories?day=${today()}`, { silent: true }); } catch { return null; }
    if (!data.posts.length) return null;
    return h("section", { class: "card so-memories", "aria-label": "On this day" },
      h("div", { class: "so-section-head" }, h("h2", { class: "suite-h2", text: "On this day" }), h("span", { class: "muted small", text: "Your posts from earlier years" })),
      h("div", { class: "so-memory-row" }, ...data.posts.map((post) => h("a", { class: "so-memory", href: `#/social?tab=feed&post=${post.id}` },
        post.photos.length ? h("img", { src: post.photos[0], alt: (post.alts || [])[0] || "", class: post.filter ? `so-filter-${post.filter}` : "" }) : h("span", { class: "so-memory-words", text: post.text.slice(0, 120) }),
        h("span", { class: "so-memory-when", text: post.years_ago === 1 ? "1 year ago" : `${post.years_ago} years ago` })))));
  }
  // What makes a profile feel finished, and the next step for each.
  const completeness = (me, person) => [
    { done: isPicture(me.avatar), label: "Add a profile picture", run: () => changePicture(async (ref) => { await api("/me", { method: "PATCH", body: { avatar: ref } }); Aira.router.render(); }) },
    { done: !!me.cover, label: "Add a cover photo", run: () => changePicture(async (ref) => { await api("/me", { method: "PATCH", body: { cover: ref } }); Aira.router.render(); }) },
    { done: !!me.bio, label: "Write a few words about you", run: () => go({ tab: "profile", edit: "1" }) },
    { done: (me.links || []).length > 0, label: "Add a link to your bio", run: () => go({ tab: "profile", edit: "links" }) },
    { done: (person.posts_count || 0) > 0, label: "Share your first post", run: () => { const box = document.querySelector("[data-so-composer]"); if (box) box.focus(); else go({ tab: "feed", new: "post" }); } },
    { done: (person.friends || 0) >= 3, label: "Find three friends", run: () => go({ tab: "friends" }) },
  ];
  function nudgeCard(me, person) {
    const steps = completeness(me, person);
    const done = steps.filter((step) => step.done).length;
    if (done === steps.length) return null;
    const pct = Math.round((100 * done) / steps.length);
    return h("section", { class: "card so-nudge", "aria-label": "Finish your profile" },
      h("div", { class: "so-section-head" }, h("h2", { class: "suite-h2", text: "Finish your profile" }), h("span", { class: "muted small", text: `${pct}% done` })),
      h("div", { class: "so-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct), "aria-label": "Profile complete" }, h("span", { style: { width: `${pct}%` } })),
      h("ul", { class: "so-steps" }, ...steps.filter((step) => !step.done).slice(0, 3).map((step) => h("li", {}, h("button", { type: "button", class: "so-link-btn", text: step.label, onclick: (event) => Aira.withBusy(event.currentTarget, step.run) })))));
  }
  const followOrFriend = (person, after) => {
    if (person.public && person.relation === "none") return S.button("Follow", async () => { await api(`/people/${person.id}/follow`, { method: "POST", body: {} }); Aira.toast(`You follow ${person.name}.`, "success"); await after(); }, { kind: "secondary sm" });
    return relationButtons(person, person.relation || "none", after)[0] || null;
  };
  function peopleCard(people, after, title = "People you may know") {
    if (!people.length) return null;
    return h("section", { class: "card so-side-card" }, h("h2", { class: "suite-h2", text: title }),
      h("ul", { class: "so-side-people" }, ...people.slice(0, 5).map((person) => h("li", {}, face(person, 40), h("div", { class: "so-grow so-ellipsis" }, personLink(person), h("div", { class: "muted small", text: `@${person.handle}` })), followOrFriend(person, after)))),
      h("a", { href: "#/social?tab=explore", class: "small", text: "See more in Explore" }));
  }
  const tagChips = (tags) => h("div", { class: "so-chips" }, ...tags.map((item) => h("a", { class: "so-chip-link", href: `#/social?tab=feed&tag=${encodeURIComponent(item.tag)}`, text: `#${item.tag}` }, item.posts ? h("span", { class: "muted small", text: ` ${item.posts}` }) : null)));

  async function feedTab(body, me, route) {
    if (route.query.post) {
      body.append(h("div", { class: "btn-row" }, S.button("Back to Home", () => go({ tab: "feed" }), { kind: "ghost", icon: "soLeft" })));
      const { ctx } = postList(body, me, async () => [(await api(`/posts/${route.query.post}`)).post], { open: true });
      await ctx.reload();
      return;
    }
    if (route.query.tag) {
      body.append(h("div", { class: "so-section-head" }, h("h2", { class: "suite-h2", text: `#${route.query.tag}` }), S.button("Back to Home", () => go({ tab: "feed" }), { kind: "ghost", icon: "soLeft" })));
      const { ctx } = postList(body, me, async () => (await api(`/tags?tag=${encodeURIComponent(route.query.tag)}`)).posts, { empty: Aira.ui.empty("No posts with this tag", "Posts you can see with this tag appear here.") });
      await ctx.reload();
      return;
    }
    const main = h("div", { class: "so-main" });
    const side = h("aside", { class: "so-side", "aria-label": "Suggestions" });
    body.append(h("div", { class: "so-home" }, main, side));
    main.append(h("section", { class: "card so-stories-card", "aria-label": "Stories" }, await storiesStrip(me)));
    const memories = await memoriesCard();
    let now = "";
    const listHost = h("div", { class: "suite-body" });
    const wrap = postList(listHost, me, async () => { const data = await api("/feed"); now = data.now; return data.posts; }, { empty: Aira.ui.empty("Your feed is empty", "Add friends, follow pages or join groups: their posts appear here.", h("div", { class: "btn-row", style: { justifyContent: "center" } }, S.button("Find friends", () => go({ tab: "friends" }), { kind: "secondary sm" }), S.button("Explore", () => go({ tab: "explore" }), { kind: "ghost sm" }), S.button("Discover groups", () => go({ tab: "groups" }), { kind: "ghost sm" }))) });
    main.append(...kids(composer(me, { onPosted: () => wrap.ctx.reload() }), memories, listHost));
    await wrap.ctx.reload();
    // Older posts exist only when the first page came back full.
    const older = S.button("Show older posts", async () => {
      const oldest = wrap.state.posts[wrap.state.posts.length - 1];
      if (!oldest) return;
      const data = await api(`/feed?before=${encodeURIComponent(oldest.created_at)}`);
      wrap.state.posts.push(...data.posts.filter((post) => !wrap.state.posts.some((item) => item.id === post.id)));
      wrap.ctx.redraw();
      if (data.posts.length < PAGE_SIZE) older.hidden = true;
    }, { kind: "ghost" });
    older.hidden = wrap.state.posts.length < PAGE_SIZE;
    main.append(h("div", { class: "btn-row" }, older));
    live(listHost, async () => {
      const changed = await api(`/feed?since=${encodeURIComponent(now)}`, { silent: true });
      now = changed.now;
      if (!changed.posts.length && !changed.removed.length) return;
      for (const id of changed.removed) wrap.state.posts = wrap.state.posts.filter((post) => post.id !== id);
      for (const post of changed.posts) {
        const index = wrap.state.posts.findIndex((item) => item.id === post.id);
        if (index >= 0) wrap.state.posts[index] = post; else wrap.state.posts.unshift(post);
      }
      wrap.state.posts.sort((a, b) => (b.created_at > a.created_at ? 1 : -1));
      wrap.ctx.redraw();
    }, 5000);
    // the column beside the feed: finish your profile, people to meet, popular #tags
    const drawSide = async () => {
      const [mine, explore] = await Promise.all([api(`/people/${me.id}`, { silent: true }).catch(() => null), api("/explore", { silent: true }).catch(() => null)]);
      side.replaceChildren(...kids(
        mine ? nudgeCard(me, mine.person) : null,
        explore ? peopleCard(explore.people, drawSide) : null,
        explore && explore.tags.length ? h("section", { class: "card so-side-card" }, h("h2", { class: "suite-h2", text: "Popular #tags" }), tagChips(explore.tags.slice(0, 10))) : null));
    };
    drawSide();
  }

  // ---------------------------------------------------------------------------
  // Explore: popular posts as a grid, people to follow, #tags, and a search across people, posts, groups, pages, tags
  // ---------------------------------------------------------------------------
  const tile = (item) => h("a", { class: `so-tile ${item.photo ? "" : "so-tile-words"}`.trim(), href: `#/social?tab=feed&post=${item.id}`, "aria-label": `${item.author.name}: ${item.text || item.alt || "photo"}` },
    item.photo ? h("img", { src: item.photo, alt: item.alt || "", loading: "lazy", class: item.filter ? `so-filter-${item.filter}` : "" }) : h("span", { class: "so-tile-text", text: item.text || "…" }),
    item.photos > 1 ? h("span", { class: "so-tile-badge", "aria-hidden": "true", text: "▣" }) : item.poll ? h("span", { class: "so-tile-badge", "aria-hidden": "true", text: "📊" }) : null,
    h("span", { class: "so-tile-over", "aria-hidden": "true" }, h("span", { text: `♥ ${short(item.reactions)}` }), h("span", { text: `💬 ${short(item.comments)}` })));
  const tiles = (items, empty) => (items.length ? h("div", { class: "so-grid" }, ...items.map(tile)) : empty);
  function peopleScroller(people, after) {
    return h("div", { class: "so-people-row", role: "list" }, ...people.map((person) => h("div", { class: "so-person-card", role: "listitem" },
      face(person, 64), personLink(person), h("span", { class: "muted small so-ellipsis", text: person.followers ? `${count(person.followers, "follower", "followers")}` : `@${person.handle}` }), followOrFriend(person, after))));
  }
  async function exploreTab(body, me, route) {
    const q = h("input", { type: "search", value: route.query.q || "", placeholder: "Search people, posts, groups, pages and #tags", "aria-label": "Search Social" });
    const results = h("div", { class: "suite-body" });
    const run = async () => {
      const words = q.value.trim();
      if (words.length < 2) { await drawHome(); return; }
      const data = await api(`/explore/search?q=${encodeURIComponent(words)}`);
      const rerun = () => run();
      const parts = kids(
        data.people.length ? S.section(`People (${data.people.length})`, h("ul", { class: "life-people" }, ...data.people.map((person) => personRow(person, relationButtons(person, person.relation, rerun))))) : null,
        data.tags.length ? S.section("Tags", tagChips(data.tags)) : null,
        data.posts.length ? S.section(`Posts (${data.posts.length})`, tiles(data.posts)) : null,
        data.groups.length ? S.section("Groups", h("ul", { class: "life-lines" }, ...data.groups.map((group) => groupLine(group)))) : null,
        data.pages.length ? S.section("Pages", h("ul", { class: "life-lines" }, ...data.pages.map((page) => pageLine(page)))) : null);
      results.replaceChildren(...(parts.length ? parts : [S.section("", Aira.ui.empty(`Nothing matches “${words}”`, "Try fewer words, part of a name, or a #tag.", S.button("Find friends", () => go({ tab: "friends", q: words }), { kind: "secondary sm" })))]));
    };
    const drawHome = async () => {
      const data = await api("/explore");
      results.replaceChildren(...kids(
        data.tags.length ? h("section", { class: "so-explore-tags", "aria-label": "Popular tags" }, tagChips(data.tags)) : null,
        data.people.length ? S.section("People to follow", peopleScroller(data.people, drawHome)) : null,
        S.section("Popular now", tiles(data.trending, Aira.ui.empty("Nothing popular yet", "Public posts with the most reactions and comments of the last two weeks show here. Share one!", S.button("Write a post", () => go({ tab: "feed", new: "post" }), { kind: "secondary sm" }))))));
    };
    q.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); run(); } });
    q.addEventListener("search", () => { if (!q.value) run(); });
    body.append(h("div", { class: "so-searchbar" }, Aira.icon("jobs"), q, S.button("Search", run, { kind: "secondary sm" })), results);
    await run();
  }

  // ---------------------------------------------------------------------------
  // Reels: tall videos from the Videos app, one after another; like, comment and share through Videos
  // ---------------------------------------------------------------------------
  const videos = (path, opts) => S.api("videos", path, opts);
  async function reelComments(reel) {
    const data = await videos(`/videos/${reel.id}/comments`);
    const list = h("ul", { class: "so-comments" }, ...data.comments.map((item) => h("li", { class: "so-comment" }, face(item.author, 30), h("div", { class: "so-comment-main" }, h("div", { class: "so-bubble" }, h("strong", { text: item.author.name }), h("span", { class: "so-bubble-text", text: item.text })), h("span", { class: "muted small", text: when(item.created_at) })))));
    const input = h("input", { type: "text", maxlength: "1000", placeholder: "Add a comment", "aria-label": "Your comment on the reel" });
    const dialog = Aira.modal({ title: `Comments on “${reel.title}”`, body: h("div", { class: "suite-body" }, data.comments.length ? list : h("p", { class: "muted", text: "No comments yet. Be the first." }), input), actions: [{ label: "Close", kind: "ghost" }, { label: "Comment", kind: "primary", onClick: async () => { if (!input.value.trim()) { input.focus(); return false; } await videos(`/videos/${reel.id}/comments`, { method: "POST", body: { text: input.value } }); reel.comments += 1; Aira.toast("Comment added.", "success"); return true; } }] });
    input.focus();
    return dialog;
  }
  async function reelsTab(body) {
    const create = h("a", { class: "btn primary sm", href: "#/videos?tab=studio&new=reel" }, Aira.icon("soPlus"), h("span", { text: "Create a reel" }));
    let data;
    try { data = await videos("/reels", { silent: true }); } catch (error) {
      body.append(S.section("", Aira.ui.empty("Reels come from the Videos app", error.status === 402 || error.status === 403 ? "Videos is not open to you here. Ask whoever runs your account to turn it on." : "Videos could not be reached just now. Try again in a moment.", h("a", { class: "btn secondary sm", href: "#/videos", text: "Open Videos" }))));
      return;
    }
    if (!data.reels.length) { body.append(S.section("", Aira.ui.empty("No reels yet", "A reel is a short tall video filmed on a phone held upright. Share the first one!", create))); return; }
    let muted = true;
    const stage = h("div", { class: "so-reels", tabindex: "0", "aria-label": "Reels. Scroll, swipe or use the arrow keys to move between them." });
    const players = [];
    for (const reel of data.reels) {
      const player = h("video", { src: reel.stream_url, poster: reel.thumb_url || undefined, loop: true, playsinline: true, muted: true, preload: "metadata", "aria-label": reel.title });
      player.muted = true;
      players.push(player);
      const likeN = h("span", { text: short(reel.likes) });
      const like = h("button", { type: "button", class: `so-reel-btn ${reel.liked ? "on" : ""}`.trim(), "aria-pressed": String(!!reel.liked), "aria-label": `Like ${reel.title}` }, Aira.icon("soHeart"), likeN);
      like.addEventListener("click", (event) => Aira.withBusy(event.currentTarget, async () => { const result = await videos(`/videos/${reel.id}/like`, { method: "POST", body: { on: !reel.liked } }); reel.liked = result.liked; reel.likes = result.likes; likeN.textContent = short(reel.likes); like.classList.toggle("on", reel.liked); like.setAttribute("aria-pressed", String(reel.liked)); }));
      const commentN = h("span", { text: short(reel.comments) });
      const comment = h("button", { type: "button", class: "so-reel-btn", "aria-label": `Comments on ${reel.title}`, onclick: async () => { await reelComments(reel); commentN.textContent = short(reel.comments); } }, Aira.icon("soChat"), commentN);
      const share = h("button", { type: "button", class: "so-reel-btn", "aria-label": `Share ${reel.title}`, onclick: () => S.copy(S.absolute(`/#/videos?tab=reels&start=${reel.id}`)) }, Aira.icon("soShare"), h("span", { text: "Share" }));
      const sound = h("button", { type: "button", class: "so-reel-btn", "aria-label": "Sound on", onclick: () => { muted = !muted; for (const item of players) item.muted = muted; for (const button of stage.querySelectorAll(".so-reel-sound")) { button.replaceChildren(Aira.icon(muted ? "soMute" : "soSound")); button.setAttribute("aria-label", muted ? "Sound on" : "Sound off"); } } }, Aira.icon("soMute"));
      sound.classList.add("so-reel-sound");
      player.addEventListener("click", () => { if (player.paused) player.play().catch(() => {}); else player.pause(); });
      stage.append(h("section", { class: "so-reel", "aria-label": reel.title }, player,
        h("div", { class: "so-reel-info" }, h("a", { href: `#/videos?channel=${reel.channel.id}`, class: "so-reel-channel" }, face(reel.channel, 30), h("strong", { text: reel.channel.name })), h("span", { text: reel.title })),
        h("div", { class: "so-reel-actions" }, like, comment, share, sound, h("a", { class: "so-reel-btn", href: `#/videos?tab=reels&start=${reel.id}`, "aria-label": "Open in Videos" }, Aira.icon("videos"), h("span", { text: "Videos" })))));
    }
    // the reel in view plays; the others wait
    const watcher = new IntersectionObserver((entries) => { for (const entry of entries) { const player = entry.target.querySelector("video"); if (!player) continue; if (entry.isIntersecting && entry.intersectionRatio > 0.6) player.play().catch(() => {}); else player.pause(); } }, { root: stage, threshold: [0, 0.6, 1] });
    for (const section of stage.children) watcher.observe(section);
    stage.addEventListener("keydown", (event) => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); stage.scrollBy({ top: (event.key === "ArrowDown" ? 1 : -1) * stage.clientHeight, behavior: "smooth" }); } });
    body.append(h("div", { class: "so-reels-wrap" }, stage, h("div", { class: "so-reels-side" }, create, h("p", { class: "muted small", text: "Reels come from the Videos app: whatever you like or comment on here counts there too." }))));
  }

  // ---------------------------------------------------------------------------
  // Chats
  // ---------------------------------------------------------------------------
  async function newChat() {
    const friends = (await api("/friends")).friends;
    if (!friends.length) { Aira.toast("Add friends first: you can message your friends.", "info"); go({ tab: "friends" }); return; }
    await S.edit({
      title: "New chat",
      fields: [
        { name: "title", label: "Group name (only for a group chat)", placeholder: "Weekend plans" },
        { type: "heading", label: "Who (tick one for a private chat, more for a group)" },
        ...friends.map((friend) => ({ name: `with_${friend.id}`, label: `${/^https:/i.test(friend.avatar) ? "" : friend.avatar} ${friend.name} (@${friend.handle})`, type: "checkbox" })),
      ],
      submitLabel: "Start chatting",
      onSave: async (values) => {
        const chosen = friends.filter((friend) => values[`with_${friend.id}`]).map((friend) => friend.id);
        if (!chosen.length) throw new Error("Tick at least one friend.");
        const created = chosen.length === 1 && !String(values.title || "").trim()
          ? await api("/chats", { method: "POST", body: { person_id: chosen[0] } })
          : await api("/chats", { method: "POST", body: { title: values.title || "Group chat", member_ids: chosen } });
        go({ tab: "chats", chat: created.chat.id });
      },
    });
  }

  async function chatsTab(body, me, route) {
    const narrow = window.matchMedia && window.matchMedia("(max-width: 900px)").matches;
    const chatId = route.query.chat || "";
    const listBox = h("nav", { "aria-label": "Chats", style: { display: "grid", gap: "6px", alignContent: "start" } });
    const drawList = (data) => listBox.replaceChildren(
      h("div", { class: "so-list-head" }, S.button("New chat", newChat, { icon: "plus" })),
      ...(data.chats.length ? data.chats.map((chat) => h("a", { href: `#/social?tab=chats&chat=${chat.id}`, class: `btn ${chat.id === chatId ? "secondary" : "ghost"}`, "aria-current": chat.id === chatId ? "true" : "false", style: { justifyContent: "flex-start", gap: "10px", minHeight: "56px" } },
        chat.kind === "group" ? h("span", { "aria-hidden": "true", style: { fontSize: "24px" }, text: "👥" }) : face({ avatar: chat.avatar }, 36),
        h("span", { style: { flex: "1", textAlign: "left", minWidth: "0" } }, h("strong", { text: chat.title }), chat.last ? h("span", { class: "muted small", style: { display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, text: `${chat.last.mine ? "You: " : ""}${chat.last.deleted ? "Message deleted" : chat.last.text}` }) : null),
        chat.muted ? h("span", { class: "muted small", text: "Muted" }) : null,
        chat.unread ? h("span", { "aria-label": `${chat.unread} unread`, text: String(chat.unread), style: { background: "var(--brand)", color: "var(--on-brand)", borderRadius: "999px", padding: "1px 8px", fontSize: "13px" } }) : null)) : [narrow ? Aira.ui.empty("No chats yet", "Chat with one friend, or start a group chat for the family or a club.") : h("p", { class: "muted", text: "No chats yet." })]));
    let listData = await api("/chats");
    drawList(listData);
    if (!chatId) {
      // A wide screen keeps the conversation's place beside the list, like a messenger.
      body.append(narrow ? S.section("", listBox) : h("div", { class: "so-chat-grid" }, S.section("", listBox),
        h("section", { class: "card suite-section so-chat-pick" }, Aira.ui.empty(listData.chats.length ? "Choose a chat" : "Start a conversation", listData.chats.length ? "Pick a chat on the left to read and reply." : "Message a friend, or start a group chat.", S.button("New chat", newChat, { kind: "secondary sm", icon: "plus" })))));
      live(listBox, async () => { listData = await api("/chats", { silent: true }); drawList(listData); }, 4000, ["chat"]);
      return;
    }

    const first = await api(`/chats/${chatId}/messages`);
    const chat = first.chat;
    const state = { messages: first.messages, now: first.now, readUpto: first.read_upto || "" };
    const thread = h("div", { role: "log", "aria-live": "polite", "aria-label": `Messages with ${chat.title}`, style: { display: "grid", gap: "10px", alignContent: "start", maxHeight: "60vh", minHeight: "240px", overflowY: "auto", padding: "6px" } });
    // Redrawn only when something changed; it follows new messages only when you are at the bottom, so you can
    // scroll up and read older ones in peace.
    const draw = (follow = false) => {
      const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80;
      for (const item of state.messages) if (item.mine) item.seen = !!state.readUpto && item.created_at <= state.readUpto;
      thread.replaceChildren(...(state.messages.length ? state.messages.map((item) => {
        const bubble = h("div", { style: { maxWidth: "100%", padding: "10px 14px", borderRadius: "18px", background: item.mine ? "var(--brand)" : "var(--surface-2)", color: item.mine ? "var(--on-brand)" : "var(--ink)", fontSize: "16px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" } },
          item.deleted ? h("em", { text: "This message was deleted." }) : null,
          item.held ? h("em", { text: "Waiting for a safety check: not delivered yet." }) : null,
          item.text ? h("div", { text: item.text }) : null,
          item.photo ? h("a", { href: item.photo, target: "_blank", rel: "noopener noreferrer" }, h("img", { src: item.photo, alt: `Photo from ${item.author.name}`, loading: "lazy", referrerpolicy: "no-referrer", style: { maxWidth: "100%", borderRadius: "10px", marginTop: "6px" } })) : null);
        return h("div", { style: { display: "flex", gap: "8px", alignItems: "flex-end", justifyContent: item.mine ? "flex-end" : "flex-start" } },
          item.mine ? null : face(item.author, 30),
          h("div", { style: { display: "grid", gap: "2px", maxWidth: "min(80%, 560px)", minWidth: "0", justifyItems: item.mine ? "end" : "start" } },
            item.mine || chat.kind === "direct" ? null : h("span", { class: "muted small", text: item.author.name }),
            bubble,
            h("span", { class: "muted small" }, h("span", { text: `${when(item.created_at)}${item.mine && !item.deleted ? (item.seen ? " · Seen" : " · Sent") : ""}` }),
              item.can_delete ? h("button", { type: "button", class: "btn ghost sm", text: "Delete for everyone", onclick: async () => { await api(`/messages/${item.id}`, { method: "DELETE" }); await poll(); } }) : null,
              !item.mine && !item.deleted ? h("button", { type: "button", class: "btn ghost sm", text: "Report", "aria-label": "Report this message", onclick: () => report("message", item.id) }) : null)));
      }) : [Aira.ui.empty("No messages yet", "Say hello!")]));
      if (follow || atBottom) thread.scrollTop = thread.scrollHeight;
    };
    const markRead = () => api(`/chats/${chatId}/read`, { method: "POST", body: {}, silent: true }).catch(() => {});
    let ticks = 0;
    const poll = async (follow = false) => {
      const data = await api(`/chats/${chatId}/messages?since=${encodeURIComponent(state.now)}`, { silent: true });
      state.now = data.now;
      let changed = follow;
      for (const item of data.messages) {
        const index = state.messages.findIndex((m) => m.id === item.id);
        if (item.removed) { if (index >= 0) state.messages.splice(index, 1); changed = true; continue; }
        if (index >= 0) state.messages[index] = item; else state.messages.push(item);
        changed = true;
      }
      if (data.messages.length) markRead();
      // "Seen" moves when the others read, without new messages
      if ((data.read_upto || "") !== state.readUpto) { state.readUpto = data.read_upto || ""; changed = true; }
      // the list beside the chat (wide screens) shows the other chats' new messages
      ticks += 1;
      if (!narrow && ticks % 5 === 0) { listData = await api("/chats", { silent: true }); drawList(listData); }
      if (changed) draw(follow);
    };
    draw();
    markRead();

    const closed = !chat.can_send;
    const input = h("textarea", { rows: "2", placeholder: chat.blocked ? "You cannot message this person." : closed ? "You can message friends, and people who accept messages from everyone." : `Message ${chat.title}`, "aria-label": "Your message", maxlength: "4000", disabled: closed, style: { fontSize: "16px" } });
    const send = async () => {
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      const sent = await api(`/chats/${chatId}/messages`, { method: "POST", body: { text } });
      input.value = "";
      if (sent && sent.held) Aira.toast(sent.notice || "This message is waiting for a safety check.", "info");
      // Someone who writes about hurting themselves sees where to get help at once.
      if (sent && sent.support) Aira.modal({ title: "You are not alone", body: h("p", { style: { whiteSpace: "pre-wrap" }, text: sent.support }), actions: [{ label: "Close", kind: "primary" }] });
      await poll(true);
      input.focus();
    };
    input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } });
    const other = chat.kind === "direct" ? chat.members[0] : null;
    const head = h("div", { style: { display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" } },
      narrow ? S.button("All chats", () => go({ tab: "chats" }), { kind: "ghost sm" }) : null,
      chat.kind === "group" ? h("span", { "aria-hidden": "true", style: { fontSize: "26px" }, text: "👥" }) : face({ avatar: chat.avatar }, 36),
      h("div", { style: { flex: "1", minWidth: "0" } }, h("h2", { class: "suite-h2", style: { margin: 0 }, text: chat.title }), chat.kind === "group" ? h("span", { class: "muted small", text: chat.members.map((m) => m.name).join(", ") }) : null),
      h("div", { class: "btn-row" },
        S.button(chat.muted ? "Unmute" : "Mute", async () => { await api(`/chats/${chatId}/mute`, { method: "POST", body: { muted: !chat.muted } }); Aira.router.render(); }, { kind: "ghost sm" }),
        chat.kind === "group" && chat.admin ? S.button("Add people", async () => {
          const friends = (await api("/friends")).friends.filter((f) => !chat.members.some((m) => m.id === f.id));
          if (!friends.length) { Aira.toast("All your friends are already here.", "info"); return; }
          await S.edit({ title: "Add people", fields: [{ name: "person_id", label: "Friend", type: "select", options: friends.map((f) => [f.id, `${f.name} (@${f.handle})`]) }], values: { person_id: friends[0].id }, submitLabel: "Add", onSave: async (values) => { await api(`/chats/${chatId}/members`, { method: "POST", body: { person_id: values.person_id } }); Aira.router.render(); } });
        }, { kind: "ghost sm" }) : null,
        chat.kind === "group" && chat.admin && chat.members.length ? S.button("Remove someone", () => S.edit({ title: "Remove someone from the chat", fields: [{ name: "person_id", label: "Person", type: "select", options: chat.members.map((m) => [m.id, m.name]) }], values: { person_id: chat.members[0].id }, submitLabel: "Remove", onSave: async (values) => { await api(`/chats/${chatId}/members/${values.person_id}`, { method: "DELETE" }); Aira.router.render(); } }), { kind: "ghost sm" }) : null,
        other ? S.button("Profile", () => go({ tab: "profile", person: other.id }), { kind: "ghost sm" }) : null,
        other && !chat.blocked ? S.button("Block", async () => { if (await block(other)) Aira.router.render(); }, { kind: "ghost sm" }) : null,
        S.button(chat.kind === "group" ? "Leave group" : "Hide chat", async () => { if (await Aira.confirm({ title: chat.kind === "group" ? "Leave this group chat?" : "Hide this chat?", message: chat.kind === "group" ? "You stop getting its messages." : "It comes back when a new message arrives.", confirmLabel: chat.kind === "group" ? "Leave" : "Hide", danger: chat.kind === "group" })) { await api(`/chats/${chatId}/leave`, { method: "POST", body: {} }); go({ tab: "chats" }); } }, { kind: "ghost sm" })));
    // a photo from the device (or, under it, a photo link)
    const photoButton = S.button("Send a photo", async () => {
      const [file] = await choosePhotos(false);
      if (!file) return;
      const photo = await uploadPhoto(file);
      await api(`/chats/${chatId}/messages`, { method: "POST", body: { photo: photo.ref } });
      await poll(true);
    }, { kind: "ghost", icon: "soPhoto" });
    const linkButton = null; // photos come from the device only: a link could tell its website who looks at it
    const sendButton = h("button", { type: "button", class: "btn", disabled: closed, style: { minHeight: "52px", minWidth: "110px", fontSize: "17px" }, text: "Send", onclick: (event) => Aira.withBusy(event.currentTarget, send) });
    const about = chat.listing_id ? h("p", { class: "muted small", style: { margin: 0 } }, h("span", { text: "This chat is about something on the Marketplace. " }), h("a", { href: `#/social?tab=market&listing=${chat.listing_id}`, text: "See the listing" })) : null;
    const threadCard = h("section", { class: "card suite-section", style: { minWidth: "0" } }, ...kids(head, about, thread,
      h("div", { style: { display: "flex", gap: "8px", alignItems: "flex-end", flexWrap: "wrap" } }, h("div", { style: { flex: "1 1 240px" } }, input), closed ? null : photoButton, closed ? null : linkButton, sendButton)));
    body.append(narrow ? threadCard : h("div", { class: "so-chat-grid" }, S.section("", listBox), threadCard));
    thread.scrollTop = thread.scrollHeight;
    live(thread, poll, 2000, ["chat"]);
  }

  // ---------------------------------------------------------------------------
  // Friends
  // ---------------------------------------------------------------------------
  const relationButtons = (person, relation, after) => {
    if (relation === "friends") return [S.button(`Message ${person.name}`, () => message(person), { kind: "secondary sm" })];
    if (relation === "request_received") return [S.button("Accept", async () => { await api(`/friends/${person.id}`, { method: "POST", body: {} }); Aira.toast(`You and ${person.name} are friends now.`, "success"); await after(); }, { kind: "sm" }), S.button("Decline", async () => { await api(`/friends/${person.id}`, { method: "DELETE" }); await after(); }, { kind: "ghost sm" })];
    if (relation === "request_sent") return [S.button("Cancel request", async () => { await api(`/friends/${person.id}`, { method: "DELETE" }); await after(); }, { kind: "ghost sm" })];
    if (relation === "awaiting_parent") return [h("span", { class: "muted small", text: "Waiting for a grown-up to say yes" }), S.button("Cancel", async () => { await api(`/friends/${person.id}`, { method: "DELETE" }); await after(); }, { kind: "ghost sm" })];
    if (relation === "self") return [];
    return [S.button("Add friend", async () => {
      const result = await api(`/friends/${person.id}`, { method: "POST", body: {} });
      Aira.toast(result.relation === "awaiting_parent" ? "A grown-up in your family will say yes or no first." : "Friend request sent.", "success");
      await after();
    }, { kind: "secondary sm" })];
  };
  // A grown-up's answer to a child's new friend.
  // A guardian's list of the adults outside the family who may contact this child or teen.
  const approvedContacts = async (child) => {
    const box = h("div", { class: "suite-form" });
    const draw = async () => {
      const data = await api(`/guardian/${child.id}/contacts`);
      const handle = h("input", { type: "text", placeholder: "@name", "aria-label": "Their Social name" });
      box.replaceChildren(
        data.contacts.length ? h("ul", { class: "life-people" }, ...data.contacts.map((person) => personRow(person, S.button("Remove", async () => { await api(`/guardian/${child.id}/contacts/${person.id}`, { method: "DELETE" }); await draw(); }, { kind: "ghost sm" })))) : h("p", { class: "muted", text: "Nobody yet." }),
        data.can_change ? h("div", { class: "btn-row" }, handle, S.button("Approve", async () => { try { await api(`/guardian/${child.id}/contacts`, { method: "POST", body: { handle: handle.value } }); await draw(); } catch (error) { Aira.reportError(error); } }, { kind: "sm" })) : null);
    };
    await draw();
    Aira.modal({ title: `Approved contacts of ${child.name}`, body: box, actions: [{ label: "Done", kind: "primary" }] });
  };

  const guardianRow = (item, after) => h("li", { class: "life-person", style: { display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" } },
    face(item.other, 40),
    h("div", { style: { flex: "1 1 220px", minWidth: "0" } },
      h("strong", { text: `${item.child.name} ${item.action === "accept" ? "wants to accept a request from" : "wants to be friends with"} ` }), personLink(item.other), h("span", { class: "muted small", text: ` @${item.other.handle}` }),
      h("div", { class: "muted small", text: [item.other.young ? "Also young" : "An adult", item.other.friends_with_you ? "your friend" : "", item.other.mutual_friends ? count(item.other.mutual_friends, "friend in common with you", "friends in common with you") : ""].filter(Boolean).join(" · ") })),
    S.button("Say yes", async () => { await api(`/guardian/${item.child.id}/${item.other.id}`, { method: "POST", body: { approve: true } }); Aira.toast(`${item.child.name} can be friends with ${item.other.name}.`, "success"); await after(); }, { kind: "sm" }),
    S.button("Say no", async () => { await api(`/guardian/${item.child.id}/${item.other.id}`, { method: "POST", body: { approve: false } }); await after(); }, { kind: "ghost sm" }));
  const personRow = (person, ...buttons) => h("li", { class: "life-person", style: { display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" } }, face(person, 40), h("div", { style: { flex: "1", minWidth: "0" } }, personLink(person), h("span", { class: "muted small", text: ` @${person.handle}` }), person.headline ? h("div", { class: "muted small", text: person.headline }) : null), ...buttons.flat().filter(Boolean));
  const closeToggle = (person, on, after) => h("button", { type: "button", class: `btn sm ${on ? "secondary" : "ghost"}`, "aria-pressed": String(on), title: on ? "On your close friends list" : "Add to close friends", onclick: (event) => Aira.withBusy(event.currentTarget, async () => { await api(`/people/${person.id}/close`, { method: on ? "DELETE" : "POST", body: {} }); Aira.toast(on ? `${person.name} is no longer a close friend.` : `${person.name} is on your close friends list.`, "success"); await after(); }) }, Aira.icon("soStar"), h("span", { text: on ? "Close friend" : "Add to close friends" }));

  async function friendsTab(body, me, route) {
    const q = h("input", { type: "search", value: route.query.q || "", placeholder: "Search by name or @name", "aria-label": "Search people, groups and pages" });
    const results = h("div", { class: "suite-body" });
    const runSearch = async () => {
      const query = q.value.trim();
      if (query.length < 2) { results.replaceChildren(); return; }
      const data = await api(`/search?q=${encodeURIComponent(query)}`);
      results.replaceChildren(...kids(
        data.people.length ? h("ul", { class: "life-people" }, ...data.people.map((person) => personRow(person, relationButtons(person, person.relation, runSearch)))) : h("p", { class: "muted", text: "Nobody found. Check the spelling, or ask them for their @name." }),
        data.groups.length ? h("p", {}, h("strong", { text: "Groups: " }), ...data.groups.map((g) => h("a", { href: `#/social?tab=groups&group=${g.id}`, text: `${g.name}  ` }))) : null,
        data.pages.length ? h("p", {}, h("strong", { text: "Pages: " }), ...data.pages.map((p) => h("a", { href: `#/social?tab=pages&page=${p.id}`, text: `${p.name}  ` }))) : null));
    };
    q.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); runSearch(); } });
    body.append(S.section("Find people", h("div", { style: { display: "flex", gap: "8px" } }, h("div", { style: { flex: "1" } }, q), S.button("Search", runSearch)), results));
    if (q.value) await runSearch();
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const draw = async () => {
      const [data, blockedData, close] = await Promise.all([api("/friends"), api("/blocks"), api("/close-friends")]);
      const blocked = blockedData.blocked;
      const closeIds = new Set(close.people.map((person) => person.id));
      panel.replaceChildren(...kids(
        data.children_asking && data.children_asking.length ? S.section(`Your children's new friends (${data.children_asking.length})`, h("p", { class: "muted", text: "A child's new friend from outside your family waits for a grown-up to say yes." }), h("ul", { class: "life-people" }, ...data.children_asking.map((item) => guardianRow(item, draw)))) : null,
        data.young_in_household && data.young_in_household.length ? S.section("Who may contact your children", h("p", { class: "muted", text: "Adults outside your family can befriend and message a child or a teen only if you approve them here. Teens can always talk to each other." }), h("ul", { class: "life-people" }, ...data.young_in_household.map((child) => personRow(child, S.button("Approved contacts", () => approvedContacts(child), { kind: "secondary sm" }))))) : null,
        data.incoming.length ? S.section(`Friend requests (${data.incoming.length})`, h("ul", { class: "life-people" }, ...data.incoming.map((person) => personRow(person, relationButtons(person, "request_received", draw))))) : null,
        data.awaiting_parent && data.awaiting_parent.length ? S.section("Waiting for a grown-up", h("ul", { class: "life-people" }, ...data.awaiting_parent.map((person) => personRow(person, relationButtons(person, "awaiting_parent", draw))))) : null,
        S.section(`Your friends (${data.friends.length})`, data.friends.length ? h("ul", { class: "life-people" }, ...data.friends.map((person) => personRow(person, S.button(`Message ${person.name}`, () => message(person), { kind: "secondary sm" }), closeToggle(person, closeIds.has(person.id), draw), S.button("Unfriend", async () => { if (await Aira.confirm({ title: `Unfriend ${person.name}?`, message: "You can send a new request later.", confirmLabel: "Unfriend" })) { await api(`/friends/${person.id}`, { method: "DELETE" }); await draw(); } }, { kind: "ghost sm" })))) : Aira.ui.empty("No friends yet", "Search for people you know above, or accept a request.")),
        data.friends.length ? S.section(`Close friends (${close.people.length})`, h("p", { class: "muted small", text: "Share a post or a story with only these friends: choose ⭐ Close friends when you share. Only you see this list." }), close.people.length ? h("div", { class: "so-face-row" }, ...close.people.map((person) => h("a", { href: `#/social?tab=profile&person=${person.id}`, class: "so-face-chip", title: person.name }, face(person, 36), h("span", { class: "small", text: person.name })))) : h("p", { class: "muted small", text: "Nobody yet: tap Add to close friends beside a friend." })) : null,
        data.suggestions.length ? S.section("People you may know", h("ul", { class: "life-people" }, ...data.suggestions.map((person) => personRow(person, relationButtons(person, "none", draw))))) : null,
        data.outgoing.length ? S.section("Requests you sent", h("ul", { class: "life-people" }, ...data.outgoing.map((person) => personRow(person, relationButtons(person, "request_sent", draw))))) : null,
        blocked.length ? S.section("Blocked people", h("ul", { class: "life-people" }, ...blocked.map((person) => personRow(person, S.button("Unblock", async () => { await api(`/people/${person.id}/block`, { method: "DELETE" }); await draw(); }, { kind: "ghost sm" }))))) : null));
    };
    await draw();
  }

  // ---------------------------------------------------------------------------
  // Groups and pages
  // ---------------------------------------------------------------------------
  const GROUP_FIELDS = [{ name: "name", label: "Group name", required: true, placeholder: "Neighbourhood walkers" }, { name: "about", label: "What it is about", type: "textarea", rows: 3 }, { name: "visibility", label: "Who can see it", type: "select", options: [["public", "Public: anyone can find and join"], ["private", "Private: people ask to join"]] }];
  const PAGE_FIELDS = [{ name: "name", label: "Page name", required: true, placeholder: "Asha's Bakery" }, { name: "category", label: "Category", placeholder: "Bakery" }, { name: "about", label: "About", type: "textarea", rows: 3 }, { name: "link", label: "Your website or Monosage public page (https://…)", type: "url" }];
  const startGroup = () => S.edit({ title: "Start a group", fields: GROUP_FIELDS, values: { visibility: "public" }, submitLabel: "Start the group", onSave: async (values) => { const created = await api("/groups", { method: "POST", body: values }); go({ tab: "groups", group: created.group.id }); } });
  const createPage = () => S.edit({ title: "Create a page", fields: PAGE_FIELDS, submitLabel: "Create the page", onSave: async (values) => { const created = await api("/pages", { method: "POST", body: values }); go({ tab: "pages", page: created.page.id }); } });
  // A group's or page's picture, or a stand-in emoji.
  const badge = (photo, emoji, size = 44) => (isPicture(photo) ? h("img", { class: "so-badge-img", src: photo, alt: "", style: { width: `${size}px`, height: `${size}px` } }) : h("span", { class: "so-badge-img", "aria-hidden": "true", style: { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.55)}px` }, text: emoji }));
  function groupLine(group) {
    return h("li", { class: "life-line" }, badge(group.photo, "👥"), h("div", { style: { flex: "1", minWidth: "0" } }, h("a", { href: `#/social?tab=groups&group=${group.id}`, class: "life-line-text", text: group.name }), h("span", { class: "muted life-line-sub", text: `${group.visibility === "public" ? "Public" : "Private"}${group.members !== undefined ? ` · ${count(group.members, "member", "members")}` : ""}${group.status === "pending" ? " · request sent" : ""}` })), S.button("Open", () => go({ tab: "groups", group: group.id }), { kind: "secondary sm" }));
  }
  function pageLine(page) {
    return h("li", { class: "life-line" }, badge(page.photo, "🏷️"), h("div", { style: { flex: "1", minWidth: "0" } }, h("a", { href: `#/social?tab=pages&page=${page.id}`, class: "life-line-text", text: page.name }), h("span", { class: "muted life-line-sub", text: `${page.category ? `${page.category}` : "Page"}${page.followers !== undefined ? ` · ${count(page.followers, "follower", "followers")}` : ""}${page.admin ? " · you manage it" : ""}` })), S.button("Open", () => go({ tab: "pages", page: page.id }), { kind: "secondary sm" }));
  }
  // The top of a group or page: its picture (admins change it), name and what it is.
  const heroCard = ({ photo, emoji, title, sub, about, buttons, admin, onPicture }) => h("section", { class: "card so-hero" },
    h("div", { class: "so-hero-row" },
      h("div", { class: "so-hero-pic" }, badge(photo, emoji, 84), admin ? h("button", { type: "button", class: "so-cam", "aria-label": "Change the picture", title: "Change the picture", onclick: (event) => Aira.withBusy(event.currentTarget, () => changePicture(onPicture)) }, Aira.icon("soCamera")) : null),
      h("div", { class: "so-grow" }, h("h2", { class: "suite-h2", style: { margin: 0 }, text: title }), h("p", { class: "muted", style: { margin: 0 }, text: sub }))),
    about ? h("p", { style: { whiteSpace: "pre-wrap", margin: 0 }, text: about }) : null,
    buttons);
  async function groupsTab(body, me, route) {
    if (route.query.group) {
      const draw = async () => {
        const group = (await api(`/groups/${route.query.group}`)).group;
        const editGroup = () => S.edit({ title: "Change the group", fields: GROUP_FIELDS, values: group, onSave: async (values) => { await api(`/groups/${group.id}`, { method: "PATCH", body: values }); await draw(); } });
        const deleteGroup = async () => { if (await Aira.confirm({ title: `Delete ${group.name}?`, message: "The group and its posts disappear for everyone. This cannot be undone.", confirmLabel: "Delete group", danger: true })) { await api(`/groups/${group.id}`, { method: "DELETE" }); Aira.toast("The group was deleted.", "success"); go({ tab: "groups" }); } };
        body.replaceChildren(...kids(
          h("div", { class: "btn-row" }, S.button("All groups", () => go({ tab: "groups" }), { kind: "ghost", icon: "soLeft" })),
          heroCard({ photo: group.photo, emoji: "👥", title: group.name, sub: `${group.visibility === "public" ? "Public group" : "Private group"} · ${count(group.members, "member", "members")}`, about: group.about, admin: group.admin, onPicture: async (ref) => { await api(`/groups/${group.id}`, { method: "PATCH", body: { photo: ref } }); await draw(); },
            buttons: h("div", { class: "btn-row" },
              group.status === "none" ? S.button(group.visibility === "public" ? "Join group" : "Ask to join", async () => { await api(`/groups/${group.id}/join`, { method: "POST", body: {} }); await draw(); }) : null,
              group.status === "pending" ? h("span", { class: "muted", text: "Your request is waiting for an admin." }) : null,
              group.status !== "none" ? S.button(group.status === "pending" ? "Cancel request" : "Leave group", async () => { await api(`/groups/${group.id}/leave`, { method: "POST", body: {} }); await draw(); }, { kind: "ghost" }) : null,
              group.admin ? S.button("Change group", editGroup, { kind: "ghost" }) : null,
              group.admin ? S.button("Delete group", deleteGroup, { kind: "ghost" }) : null) }),
          group.admin && group.requests && group.requests.length ? S.section("Asking to join", h("ul", { class: "life-people" }, ...group.requests.map((person) => personRow(person, S.button("Let in", async () => { await api(`/groups/${group.id}/requests/${person.id}`, { method: "POST", body: { approve: true } }); await draw(); }, { kind: "sm" }), S.button("Decline", async () => { await api(`/groups/${group.id}/requests/${person.id}`, { method: "POST", body: { approve: false } }); await draw(); }, { kind: "ghost sm" }))))) : null,
          group.status === "member" ? composer(me, { groupId: group.id, placeholder: "Write something to the group", onPosted: draw }) : null));
        if (group.posts) { const { state, ctx } = postList(body, me, async () => group.posts, { empty: Aira.ui.empty("No posts yet", "Members' posts appear here.") }); state.posts = group.posts; ctx.reload = draw; ctx.redraw(); }
        else body.append(Aira.ui.empty("This group is private", "Ask to join to see its posts."));
      };
      await draw();
      return;
    }
    const q = h("input", { type: "search", placeholder: "Find a group", "aria-label": "Find a group" });
    const panel = h("div", { class: "suite-body" });
    const draw = async () => {
      const data = await api(`/groups?q=${encodeURIComponent(q.value.trim())}`);
      panel.replaceChildren(
        S.section("Your groups", data.mine.length ? h("ul", { class: "life-lines" }, ...data.mine.map(groupLine)) : Aira.ui.empty("No groups yet", "Join a group below or start your own.")),
        S.section("Discover groups", h("div", { style: { display: "flex", gap: "8px" } }, h("div", { style: { flex: "1" } }, q), S.button("Search", draw, { kind: "secondary" })), data.discover.length ? h("ul", { class: "life-lines" }, ...data.discover.map(groupLine)) : h("p", { class: "muted", text: "No public groups found." })));
    };
    q.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); draw(); } });
    body.append(h("div", { class: "btn-row" }, me.age_group === "child" ? null : S.button("Start a group", startGroup, { icon: "plus" })), panel);
    await draw();
  }

  async function pagesTab(body, me, route) {
    if (route.query.page) {
      const draw = async () => {
        const page = (await api(`/pages/${route.query.page}`)).page;
        const editPage = () => S.edit({ title: "Change the page", fields: PAGE_FIELDS, values: page, onSave: async (values) => { await api(`/pages/${page.id}`, { method: "PATCH", body: values }); await draw(); } });
        const deletePage = async () => { if (await Aira.confirm({ title: `Delete ${page.name}?`, message: "The page and its posts disappear for everyone, and its followers stop following it.", confirmLabel: "Delete page", danger: true })) { await api(`/pages/${page.id}`, { method: "DELETE" }); Aira.toast("The page was deleted.", "success"); go({ tab: "pages" }); } };
        body.replaceChildren(...kids(
          h("div", { class: "btn-row" }, S.button("All pages", () => go({ tab: "pages" }), { kind: "ghost", icon: "soLeft" })),
          heroCard({ photo: page.photo, emoji: "🏷️", title: page.name, sub: `${page.category ? `${page.category} · ` : ""}${count(page.followers, "follower", "followers")}`, about: page.about, admin: page.admin, onPicture: async (ref) => { await api(`/pages/${page.id}`, { method: "PATCH", body: { photo: ref } }); await draw(); },
            buttons: h("div", { class: "btn-row" },
              S.button(page.following ? "Following" : "Follow", async () => { await api(`/pages/${page.id}/follow`, { method: page.following ? "DELETE" : "POST", body: {} }); await draw(); }, { kind: page.following ? "secondary" : "" }),
              /^https:\/\//i.test(page.link || "") ? h("a", { class: "btn ghost", href: page.link, target: "_blank", rel: "noopener noreferrer", text: "Visit their page" }) : null,
              page.admin ? S.button("Change page", editPage, { kind: "ghost" }) : null,
              page.admin ? S.button("Delete page", deletePage, { kind: "ghost" }) : null) }),
          page.admin ? composer(me, { pageId: page.id, placeholder: `Write as ${page.name}`, onPosted: draw }) : null));
        const { state, ctx } = postList(body, me, async () => page.posts, { empty: Aira.ui.empty("No posts yet", "The page's news appears here.") });
        state.posts = page.posts; ctx.reload = draw; ctx.redraw();
      };
      await draw();
      return;
    }
    const q = h("input", { type: "search", placeholder: "Find a page", "aria-label": "Find a page" });
    const panel = h("div", { class: "suite-body" });
    const draw = async () => {
      const data = await api(`/pages?q=${encodeURIComponent(q.value.trim())}`);
      panel.replaceChildren(
        S.section("Pages you follow", data.mine.length ? h("ul", { class: "life-lines" }, ...data.mine.map(pageLine)) : Aira.ui.empty("No pages yet", "Follow shops, clubs and services to see their news in your feed.")),
        S.section("Discover pages", h("div", { style: { display: "flex", gap: "8px" } }, h("div", { style: { flex: "1" } }, q), S.button("Search", draw, { kind: "secondary" })), data.discover.length ? h("ul", { class: "life-lines" }, ...data.discover.map(pageLine)) : h("p", { class: "muted", text: "No pages found." })));
    };
    q.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); draw(); } });
    const adult = !(me.age_group === "child" || me.age_group === "teen");
    body.append(h("div", { class: "btn-row" }, adult ? S.button("Create a page", createPage, { icon: "plus" }) : null), panel);
    await draw();
  }

  // ---------------------------------------------------------------------------
  // Marketplace: buy and sell with friends and neighbours (aira/suite/social/market.py)
  // ---------------------------------------------------------------------------
  // setup (GET /market/setup): the currencies, and the business only for someone who runs it. A listing is the
  // seller's own unless they tick "Sell it for <business>". The price is sent as typed: "ten" is an error.
  const listingFields = (setup, creating) => kids(
    { name: "title", label: "What are you selling?", required: true, placeholder: "Child's bike, 16 inch" },
    { name: "price", label: "Price (leave empty or 0 to give it away)", placeholder: "12.50" },
    creating && setup.currencies.length > 1 ? { name: "currency", label: "Currency", type: "select", options: setup.currencies.map((code) => [code, code]) } : null,
    { name: "category", label: "Category", type: "select", options: Object.entries(CATEGORY_LABELS) },
    { name: "condition", label: "Condition", type: "select", options: Object.entries(CONDITION_LABELS) },
    { name: "about", label: "Description", type: "textarea", rows: 4, placeholder: "Size, colour, what is included, why you are selling" },
    { name: "place", label: "Where to pick it up (town or area)", placeholder: "Riverside" },
    { name: "photos", label: "Photos already added (add photos from your device below)", type: "textarea", rows: 2 },
    { name: "audience", label: "Who can see it", type: "select", options: [["public", "Everyone on Social"], ["friends", "Only my friends"]] },
    { name: "payment_link", label: "Your payment link (optional, https://…)", type: "url", hint: "Buyers pay you there or in person. Monosage never handles the money." },
    setup.business ? { name: "for_business", label: `Sell it for ${setup.business.name}`, type: "checkbox", hint: `The sale counts in the Business hub.${setup.business.has_payment_link ? " Buyers see the business's payment link if you leave yours empty." : ""}` } : null,
  );
  // Photos from the device in the "Sell something" form: a picker under the photo links field, merged when saving.
  const withDevicePhotos = (existing = []) => {
    const picker = photoPicker({ withAlt: false, label: "Add photos from your device", existing });
    requestAnimationFrame(() => {
      const field = document.querySelector('.modal textarea[name="photos"]');
      if (field && field.closest(".field")) field.closest(".field").after(picker.node);
    });
    return picker;
  };
  const listingBody = (values, picker) => {
    if (picker && picker.busy()) throw new Error("Wait for the photos to finish uploading.");
    return { ...values, photos: [...photoList(values.photos), ...(picker ? picker.items().map((item) => item.ref) : [])] };
  };
  const newListing = async () => {
    const setup = await api("/market/setup");
    const picker = withDevicePhotos();
    return S.edit({
      title: "Sell something", fields: listingFields(setup, true), values: { category: "other", condition: "good", audience: "public", currency: setup.currency, for_business: false }, submitLabel: "Put it up for sale",
      onSave: async (values) => { const created = await api("/market", { method: "POST", body: listingBody(values, picker) }); Aira.toast("It is on the Marketplace.", "success"); go({ tab: "market", listing: created.listing.id }); },
    });
  };
  const listingCard = (listing) => h("a", { class: `so-listing ${listing.status === "sold" ? "so-sold" : ""}`, href: `#/social?tab=market&listing=${listing.id}`, "aria-label": `${listing.title}, ${listing.price}${listing.status !== "available" ? `, ${STATUS_LABELS[listing.status]}` : ""}` },
    listing.photos.length && isPicture(listing.photos[0])
      ? h("img", { class: "so-listing-photo", src: listing.photos[0], alt: "", loading: "lazy", referrerpolicy: "no-referrer" })
      : h("span", { class: "so-listing-photo", "aria-hidden": "true", text: "🏷️" }),
    h("span", { class: "so-listing-price", text: listing.price }),
    h("span", { class: "so-listing-title", text: listing.title }),
    h("span", { class: "muted small", text: [listing.place, CONDITION_LABELS[listing.condition]].filter(Boolean).join(" · ") }),
    listing.status !== "available" ? h("span", {}, S.pill(STATUS_LABELS[listing.status], { Reserved: "warn", Sold: "info" })) : null);
  const listingGrid = (listings, empty) => (listings.length ? h("div", { class: "so-market-grid" }, ...listings.map(listingCard)) : empty);

  async function listingDetail(body, me, id) {
    const draw = async () => {
      const listing = (await api(`/market/${id}`)).listing;
      const setStatus = async (status, extra = {}) => { await api(`/market/${listing.id}/status`, { method: "POST", body: { status, ...extra } }); await draw(); };
      const markSold = () => S.edit({
        title: "Mark as sold", submitLabel: "Mark as sold",
        fields: [{ name: "sold_price", label: "How much you got (0 if you gave it away)", required: true, placeholder: "12.50" }, { name: "buyer", label: "Who bought it (optional)", placeholder: "Ana" }],
        values: { sold_price: S.fromCents(listing.price_cents) },
        onSave: async (values) => { await setStatus("sold", { sold_price: values.sold_price, buyer: values.buyer }); Aira.toast(listing.for_business ? "Sold! It is added to the Business hub." : "Sold! It counts in your earnings from sales.", "success"); },
      });
      const ask = () => S.edit({
        title: `Message ${listing.seller.name}`, submitLabel: "Send",
        fields: [{ name: "text", label: "Your message", type: "textarea", rows: 3, required: true }],
        values: { text: `Hi! Is “${listing.title}” still available?` },
        onSave: async (values) => { const result = await api(`/market/${listing.id}/ask`, { method: "POST", body: { text: values.text } }); go({ tab: "chats", chat: result.chat.id }); },
      });
      const edit = async () => {
        const setup = await api("/market/setup");
        const uploaded = (listing.photos || []).filter((src) => src.startsWith("/api/"));
        const picker = withDevicePhotos(uploaded);
        return S.edit({ title: "Change your listing", fields: listingFields(setup, false), values: { ...listing, price: listing.price_cents ? S.fromCents(listing.price_cents) : "", payment_link: listing.own_payment_link || "", photos: (listing.photos || []).filter((src) => !src.startsWith("/api/")).join("\n") }, onSave: async (values) => { await api(`/market/${listing.id}`, { method: "PATCH", body: listingBody(values, picker) }); await draw(); } });
      };
      const remove = async () => { if (await S.confirmDelete(`“${listing.title}”`)) { await api(`/market/${listing.id}`, { method: "DELETE" }); Aira.toast("Your listing was removed.", "success"); go({ tab: "market", view: "selling" }); } };
      const link = listing.mine && listing.audience === "public" ? (await api(`/market/${listing.id}/share`)).link : null;
      body.replaceChildren(...kids(
        h("div", { class: "btn-row" }, S.button(listing.mine ? "Your listings" : "Back to the Marketplace", () => go(listing.mine ? { tab: "market", view: "selling" } : { tab: "market" }), { kind: "ghost", icon: "soLeft" })),
        S.section("", ...kids(
          photoGrid(listing.photos, listing.seller.name),
          h("div", { style: { display: "flex", gap: "10px", alignItems: "baseline", flexWrap: "wrap" } }, h("h2", { class: "suite-h2", style: { margin: 0, overflowWrap: "anywhere" }, text: listing.title }), S.pill(STATUS_LABELS[listing.status], { "For sale": "good", Reserved: "warn", Sold: "info" })),
          h("p", { class: "so-listing-price", style: { margin: 0 }, text: listing.price }),
          h("p", { class: "muted", style: { margin: 0 }, text: [CATEGORY_LABELS[listing.category], CONDITION_LABELS[listing.condition], listing.place ? `Pick up in ${listing.place}` : "", `Listed ${when(listing.created_at).replace(/^Just now$/, "just now")}`, listing.audience === "friends" ? "Friends only" : ""].filter(Boolean).join(" · ") }),
          listing.about ? richText(listing.about) : null,
          h("div", { style: { display: "flex", gap: "10px", alignItems: "center" } }, face(listing.seller, 36), h("span", {}, h("span", { class: "muted", text: "Sold by " }), personLink(listing.seller))),
          listing.mine
            ? h("div", { class: "btn-row" },
              listing.status !== "sold" ? S.button("Mark as sold", markSold) : null,
              listing.status === "available" ? S.button("Mark as reserved", () => setStatus("reserved"), { kind: "secondary" }) : null,
              listing.status !== "available" ? S.button("Available again", () => setStatus("available"), { kind: "secondary" }) : null,
              S.button("Change", edit, { kind: "ghost" }),
              S.button("Remove", remove, { kind: "ghost" }))
            : h("div", { class: "btn-row" },
              listing.can_message && listing.status !== "sold" ? S.button("Message the seller", ask) : null,
              /^https:\/\//i.test(listing.payment_link || "") && listing.status !== "sold" ? h("a", { class: "btn secondary", href: listing.payment_link, target: "_blank", rel: "noopener noreferrer", text: "Pay the seller" }) : null,
              S.button(listing.saved ? "Saved" : "Save", async () => { await api(`/market/${listing.id}/save`, { method: listing.saved ? "DELETE" : "POST", body: {} }); await draw(); }, { kind: listing.saved ? "secondary" : "ghost" }),
              S.button("Report", () => report("listing", listing.id), { kind: "ghost" })),
          !listing.mine && !listing.can_message && listing.status !== "sold" ? h("p", { class: "muted small", text: "You can message sellers who are your friends." }) : null)),
        listing.mine ? S.section("How it is going", h("p", { style: { margin: 0 }, text: `${count(listing.views || 0, "view", "views")} · saved by ${count(listing.saves || 0, "person", "people")}` }),
          listing.status === "sold" ? h("p", { style: { margin: 0 }, text: `${listing.sold_cents ? `Sold for ${listing.sold}` : "Given away"}${listing.buyer ? ` to ${listing.buyer}` : ""}.${listing.sold_cents ? (listing.for_business ? " It counts in the Business hub." : " It counts in your earnings from sales.") : ""}` }) : null,
          listing.for_business ? h("p", { class: "muted small", style: { margin: 0 }, text: "You are selling this for the business." }) : null) : null,
        listing.mine && listing.audience === "public" && listing.status !== "sold" ? S.section("Share it outside Monosage", S.share({ title: "Listing link", text: "A page anyone can open, even without Monosage: the photos, the price and a button to pay you. They can tell you they are interested.", link, onCreate: async () => (await api(`/market/${listing.id}/share`, { method: "POST", body: {} })).link, onRevoke: async () => { await api(`/market/${listing.id}/share`, { method: "DELETE" }); } })) : null));
    };
    await draw();
  }

  async function marketTab(body, me, route) {
    if (me.age_group === "child") { body.append(Aira.ui.empty("Ask a grown-up", "The Marketplace is for older people.")); return; }
    if (route.query.listing) { await listingDetail(body, me, route.query.listing); return; }
    const adult = !young(me);
    const views = [["browse", "Browse"], ["saved", "Saved"], adult ? ["selling", "Your listings"] : null].filter(Boolean);
    const view = views.some(([id]) => id === route.query.view) ? route.query.view : "browse";
    body.append(h("div", { class: "btn-row", role: "group", "aria-label": "Marketplace" },
      ...views.map(([id, label]) => h("button", { type: "button", class: `btn sm ${id === view ? "secondary" : "ghost"}`, "aria-pressed": String(id === view), style: id === view ? { textDecoration: "underline", textUnderlineOffset: "4px" } : null, text: label, onclick: () => go(id === "browse" ? { tab: "market" } : { tab: "market", view: id }) })),
      adult ? h("span", { style: { flex: "1" } }) : null,
      adult ? S.button("Sell something", newListing, { kind: "sm", icon: "plus" }) : null));
    if (view === "saved") {
      const data = await api("/market/saved");
      body.append(S.section("Saved", listingGrid(data.listings, Aira.ui.empty("Nothing saved yet", "Tap Save on a listing to keep it here."))));
      return;
    }
    if (view === "selling") {
      const data = await api("/market/mine");
      const summary = data.summary;
      body.append(
        S.kpis([
          { label: "For sale", value: summary.for_sale },
          { label: "Reserved", value: summary.reserved },
          { label: "Sold", value: summary.sold, tone: summary.sold ? "good" : "" },
          { label: "Earned from sales", value: summary.earned.length ? summary.earned.map((item) => item.text).join(" + ") : "Nothing yet", tone: summary.earned.length ? "good" : "" },
        ]),
        h("p", { class: "muted", text: "Earned from sales adds up what you marked as sold, also listings you removed afterwards. Things you sell for a business are added to the Business hub too. Buyers pay you through your payment link or in person." }),
        S.section("Your listings", listingGrid(data.listings, Aira.ui.empty("You are not selling anything yet", "Things you no longer need can earn you money: tap Sell something."))));
      return;
    }
    const q = h("input", { type: "search", placeholder: "Search: bike, sofa, piano lessons", "aria-label": "Search the Marketplace", value: route.query.q || "" });
    const category = Aira.ui.select([["", "All categories"], ...Object.entries(CATEGORY_LABELS)], route.query.category || "", { "aria-label": "Category" });
    const maxPrice = h("input", { type: "text", inputmode: "decimal", placeholder: "Highest price", "aria-label": "Highest price", value: route.query.max_price || "" });
    const sort = Aira.ui.select([["newest", "Newest first"], ["cheapest", "Cheapest first"], ["dearest", "Most expensive first"]], route.query.sort || "newest", { "aria-label": "Order" });
    const results = h("div", { class: "suite-body" });
    const search = async () => {
      const params = new URLSearchParams({ q: q.value.trim(), category: category.value, max_price: maxPrice.value.trim(), sort: sort.value });
      let data;
      try { data = await api(`/market?${params}`); } catch (error) {
        results.replaceChildren(h("p", { class: "form-error", role: "alert", text: error.status === 400 ? error.message : `The Marketplace could not be loaded. ${error.message}` }));
        if (error.status === 400 && maxPrice.value.trim()) maxPrice.focus();
        return;
      }
      const filtered = q.value.trim() || category.value || maxPrice.value.trim();
      results.replaceChildren(listingGrid(data.listings, filtered ? Aira.ui.empty("Nothing matches", "Try other words, another category or a higher price.") : Aira.ui.empty("Nothing for sale yet", adult ? "Be the first: tap Sell something." : "Things your friends and neighbours sell appear here.")));
    };
    for (const control of [q, maxPrice]) control.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); search(); } });
    for (const control of [category, sort]) control.addEventListener("change", search);
    body.append(S.section("For sale near you", h("div", { class: "so-filters" }, q, category, maxPrice, sort, S.button("Search", search, { kind: "secondary" })), results));
    await search();
  }

  // ---------------------------------------------------------------------------
  // Profile: a cover photo, the picture, links, counts (open the lists), highlights and Posts | Photos | Reels | Tagged
  // ---------------------------------------------------------------------------
  const experienceText = (items) => (items || []).map((item) => [item.title, item.company, item.start, item.end].join(" | ")).join("\n");
  const parseExperience = (text) => String(text || "").split("\n").map((line) => line.trim()).filter(Boolean).map((line) => { const [title, company = "", start = "", end = ""] = line.split("|").map((part) => part.trim()); return { title, company, start, end }; });
  const linksText = (links) => (links || []).map((item) => (item.label ? `${item.label} | ${item.url}` : item.url)).join("\n");
  const parseLinks = (text) => String(text || "").split("\n").map((line) => line.trim()).filter(Boolean).map((line) => { const parts = line.split("|").map((part) => part.trim()); return parts.length > 1 ? { label: parts[0], url: parts.slice(1).join("|") } : { url: parts[0] }; });
  const professional = (person) => (person.headline || (person.skills && person.skills.length) || (person.experience && person.experience.length) ? S.section("Work",
    person.headline ? h("p", { style: { fontSize: "17px", margin: 0 }, text: person.headline }) : null,
    person.open_to_work ? h("p", {}, S.pill("Open to work", { "Open to work": "good" })) : null,
    person.experience && person.experience.length ? h("ul", { class: "life-lines" }, ...person.experience.map((item) => h("li", { class: "life-line" }, h("div", {}, h("span", { class: "life-line-text", text: `${item.title}${item.company ? ` at ${item.company}` : ""}` }), h("span", { class: "muted life-line-sub", text: [item.start, item.end || (item.start ? "now" : "")].filter(Boolean).join(" to ") }))))) : null,
    person.skills && person.skills.length ? h("p", {}, h("strong", { text: "Skills: " }), h("span", { text: person.skills.join(", ") })) : null) : null);

  async function followList(person, which) {
    const data = await api(`/people/${person.id}/follows`);
    const rows = which === "followers" ? data.followers : data.following;
    Aira.modal({ title: which === "followers" ? `Following ${person.name}` : `${person.name} follows`, body: rows.length ? h("ul", { class: "life-people so-modal-list" }, ...rows.map((item) => personRow(item, relationButtons(item, item.relation, async () => {})))) : Aira.ui.empty(which === "followers" ? "No followers yet" : "Not following anyone yet", ""), actions: [{ label: "Close", kind: "secondary" }] });
  }
  function highlightsRow(list, mine, owner) {
    if (!list.length && !mine) return null;
    return h("div", { class: "so-highlights", role: "list", "aria-label": "Story highlights" },
      mine ? h("div", { role: "listitem" }, h("button", { type: "button", class: "so-story", onclick: () => addHighlight([], () => Aira.router.render()) }, h("span", { class: "so-story-ring so-hl-new" }, Aira.icon("soPlus")), h("span", { class: "small", text: "New" }))) : null,
      ...list.map((item) => h("div", { role: "listitem" }, h("button", { type: "button", class: "so-story", "aria-label": `Highlight: ${item.title}`, onclick: () => showStories({ author: owner, stories: item.stories, mine: !!item.mine }, () => {}, { title: `${item.title} · ${owner.name}` }) },
        h("span", { class: "so-story-ring so-hl" }, item.cover ? h("img", { class: "so-face", src: item.cover, alt: "", style: { width: "58px", height: "58px" } }) : h("span", { class: "so-face", style: { width: "58px", height: "58px", background: item.color, color: "#fff", fontSize: "22px" }, text: item.title.slice(0, 1).toUpperCase() })),
        h("span", { class: "small", text: item.title })))));
  }
  const PROFILE_VIEWS = [["posts", "Posts"], ["photos", "Photos"], ["reels", "Reels"], ["tagged", "Tagged"]];
  async function profileTab(body, me, route) {
    const self = !route.query.person || route.query.person === me.id;
    const data = await api(`/people/${self ? me.id : route.query.person}`);
    const person = data.person;
    const after = () => Aira.router.render();
    const view = PROFILE_VIEWS.some(([id]) => id === route.query.view) ? route.query.view : "posts";
    const edit = () => S.edit({
      title: "Edit your profile",
      fields: [
        { name: "display_name", label: "Your name", required: true },
        { name: "handle", label: "Your @name", required: true, hint: "3 to 30 letters, numbers, dots or underscores." },
        { name: "bio", label: "About you", type: "textarea", rows: 3 },
        { name: "links", label: "Links in your bio: one per line, as Label | https://…", type: "textarea", rows: 3, placeholder: "My shop | https://example.com" },
        { name: "avatar", label: "Picture: an emoji (or tap the camera on your picture to upload a photo)" },
        young(me) ? null : { name: "visibility", label: "Who can see your profile and public posts", type: "select", options: [["public", "Everyone"], ["private", "Only my friends"]] },
        young(me) ? null : { name: "open_messages", label: "Let people who are not my friends message me", type: "checkbox" },
      ].filter(Boolean),
      values: { ...me, links: linksText(me.links), avatar: isPicture(me.avatar) && me.avatar.startsWith("/api/") ? "" : me.avatar },
      onSave: async (values) => { const body_ = { ...values, links: parseLinks(values.links) }; if (!values.avatar && isPicture(me.avatar)) delete body_.avatar; await api("/me", { method: "PATCH", body: body_ }); after(); },
    });
    const editWork = () => S.edit({
      title: "Your work profile",
      fields: [
        { name: "headline", label: "Headline", placeholder: "Nurse at City Hospital" },
        { name: "open_to_work", label: "I am open to work", type: "checkbox" },
        { name: "skills", label: "Skills (separate with commas)", placeholder: "Patient care, Spanish" },
        { name: "experience", label: "Experience: one job per line as Title | Company | Start | End", type: "textarea", rows: 5, placeholder: "Nurse | City Hospital | 2019 | " },
      ],
      values: { ...me, skills: (me.skills || []).join(", "), experience: experienceText(me.experience) },
      onSave: async (values) => { await api("/me", { method: "PATCH", body: { headline: values.headline, open_to_work: !!values.open_to_work, skills: String(values.skills || "").split(","), experience: parseExperience(values.experience) } }); after(); },
    });
    const picture = (field) => changePicture(async (ref) => { await api("/me", { method: "PATCH", body: { [field]: ref } }); Aira.toast(field === "cover" ? "Your cover photo is up." : "Your picture is up.", "success"); after(); });
    const stat = (n, label, run) => (run ? h("button", { type: "button", class: "so-stat", onclick: run }, h("strong", { text: short(n) }), h("span", { text: label })) : h("span", { class: "so-stat" }, h("strong", { text: short(n) }), h("span", { text: label })));
    const subject = self ? { ...person, ...me } : person;
    const head = h("section", { class: "card so-profile" },
      h("div", { class: "so-cover", style: subject.cover ? { backgroundImage: `url("${subject.cover.replace(/"/g, "")}")` } : null },
        self ? h("button", { type: "button", class: "btn sm so-cover-btn", onclick: (event) => Aira.withBusy(event.currentTarget, () => picture("cover")) }, Aira.icon("soCamera"), h("span", { text: subject.cover ? "Change cover" : "Add a cover photo" })) : null),
      h("div", { class: "so-profile-main" },
        h("div", { class: "so-avatar-wrap" }, face(subject, 112), self ? h("button", { type: "button", class: "so-cam", "aria-label": "Change your profile picture", title: "Change your profile picture", onclick: (event) => Aira.withBusy(event.currentTarget, () => picture("avatar")) }, Aira.icon("soCamera")) : null),
        h("div", { class: "so-profile-who" },
          h("h2", { class: "so-profile-name", text: subject.name || me.display_name }),
          h("p", { class: "muted", style: { margin: 0 }, text: `@${subject.handle}${self ? ` · ${me.visibility === "public" ? "Public profile" : "Private profile"}` : ""}` }),
          person.close_friend ? h("span", { class: "so-chip so-chip-star", text: "⭐ Close friend" }) : null)),
      h("div", { class: "so-stats" },
        person.full ? stat(person.posts_count || 0, "posts") : null,
        stat(person.friends, "friends", self ? () => go({ tab: "friends" }) : null),
        stat(person.followers, "followers", person.full ? () => followList(person, "followers") : null),
        person.full ? stat(person.following_count || 0, "following", () => followList(person, "following")) : null),
      subject.bio ? h("p", { class: "so-bio", text: subject.bio }) : self ? h("p", { class: "muted", text: "Add a few words about yourself." }) : null,
      (subject.links || []).length ? h("div", { class: "so-links" }, ...subject.links.map((item) => h("a", { class: "so-link-chip", href: item.url, target: "_blank", rel: "noopener noreferrer nofollow", text: `🔗 ${item.label || item.url.replace(/^https:\/\//, "")}` }))) : null,
      !self && person.relation !== "friends" && person.visibility === "private" ? h("p", { class: "muted", text: "This profile is private. Become friends to see their posts." }) : null,
      self && young(me) ? h("p", { class: "muted", text: "Your profile is private: only your friends see it, and only friends can message you." }) : null,
      self
        ? h("div", { class: "btn-row" }, S.button("Edit profile", edit), S.button(young(me) ? "Work and school" : "Work profile", editWork, { kind: "secondary" }), S.button("Download my data", () => Aira.download("/api/social/me/export", `social-${me.handle}.json`), { kind: "ghost" }))
        : h("div", { class: "btn-row" }, ...relationButtons(person, person.relation, after),
          person.relation === "friends" ? closeToggle(person, person.close_friend, after) : null,
          person.can_message && person.relation !== "friends" ? S.button("Message", () => message(person), { kind: "secondary sm" }) : null,
          person.visibility === "public" && person.relation !== "friends" ? S.button(person.following ? "Unfollow" : "Follow", async () => { await api(`/people/${person.id}/follow`, { method: person.following ? "DELETE" : "POST", body: {} }); after(); }, { kind: "ghost sm" }) : null,
          S.button("Block", async () => { if (await block(person)) go({ tab: "friends" }); }, { kind: "ghost sm" }),
          S.button("Report", () => report("account", person.id), { kind: "ghost sm" })));
    body.append(...kids(head, self ? nudgeCard(me, person) : null, person.full ? highlightsRow(data.highlights || [], self, { id: person.id, name: subject.name || me.display_name, handle: person.handle, avatar: subject.avatar }) : null, professional(subject)));
    if (self && route.query.edit) { const what = route.query.edit; Aira.router.go(["social"], { tab: "profile" }, { replace: true }); if (what) setTimeout(edit, 0); return; }
    if (self && !young(me) && me.visibility === "public") {
      const link = (await api("/me/share")).link;
      body.append(S.section("Share your profile", S.share({ title: "Public profile link", text: "A page anyone can open, even without Monosage: your name, about you, your work and your public posts.", link, onCreate: async () => (await api("/me/share", { method: "POST", body: {} })).link, onRevoke: async () => { await api("/me/share", { method: "DELETE" }); } })));
    }
    if (!person.full) return;
    const query = (id) => (self ? { tab: "profile", view: id } : { tab: "profile", person: person.id, view: id });
    body.append(h("div", { class: "so-subtabs", role: "tablist", "aria-label": "What to show" }, ...PROFILE_VIEWS.map(([id, label]) => h("button", { type: "button", role: "tab", "aria-selected": String(id === view), class: id === view ? "on" : "", text: label, onclick: () => go(query(id)) }))));
    if (view === "photos" || view === "tagged") {
      const grid = (await api(`/people/${person.id}/grid?kind=${view}`)).tiles;
      body.append(tiles(grid, Aira.ui.empty(view === "photos" ? "No photos yet" : "Not tagged yet", view === "photos" ? (self ? "Share a photo from Home: it shows here." : "Photos they share appear here.") : `Posts that mention @${person.handle} appear here.`)));
      return;
    }
    if (view === "reels") {
      const none = Aira.ui.empty("No reels yet", self ? "Make a tall short video in the Videos app: it shows here." : "Their reels from the Videos app appear here.", self ? h("a", { class: "btn secondary sm", href: "#/videos?tab=studio&new=reel", text: "Create a reel" }) : null);
      if (!person.videos_channel) { body.append(none); return; }
      let reels = [];
      try { reels = ((await videos(`/channels/${person.videos_channel}`, { silent: true })).videos || []).filter((video) => video.kind === "reel"); } catch { reels = []; }
      body.append(reels.length ? h("div", { class: "so-grid so-grid-tall" }, ...reels.map((reel) => h("a", { class: "so-tile", href: `#/videos?tab=reels&start=${reel.id}`, "aria-label": reel.title }, reel.thumb_url ? h("img", { src: reel.thumb_url, alt: "" }) : h("span", { class: "so-tile-text", text: reel.title }), h("span", { class: "so-tile-over", "aria-hidden": "true" }, h("span", { text: `▶ ${short(reel.views)}` }))))) : none);
      return;
    }
    const { state, ctx } = postList(body, me, async () => (await api(`/people/${person.id}`)).posts, { empty: Aira.ui.empty(self ? "You have not posted yet" : "No posts to show", self ? "Share something from Home." : "Posts you are allowed to see appear here.") });
    state.posts = data.posts; ctx.reload = async () => { state.posts = (await api(`/people/${person.id}`)).posts; ctx.redraw(); }; ctx.redraw();
  }

  // ---------------------------------------------------------------------------
  // Saved: collections of saved posts
  // ---------------------------------------------------------------------------
  async function savedTab(body, me, route) {
    const wanted = route.query.collection || "";
    const data = await api(`/saved${wanted && wanted !== "all" ? `?collection=${encodeURIComponent(wanted)}` : ""}`);
    const newCollection = () => S.edit({ title: "New collection", fields: [{ name: "name", label: "Name", required: true, placeholder: "Recipes, Ideas, To read" }], submitLabel: "Create", onSave: async (values) => { await api("/collections", { method: "POST", body: values }); Aira.router.render(); } });
    if (!wanted) {
      body.append(h("div", { class: "so-section-head" }, h("h2", { class: "suite-h2", text: "Your collections" }), S.button("New collection", newCollection, { kind: "secondary sm", icon: "soPlus" })),
        h("div", { class: "so-collections" }, ...data.collections.map((item) => h("a", { class: "so-collection", href: `#/social?tab=saved&collection=${item.id || "all"}` },
          item.cover ? h("img", { src: item.cover, alt: "" }) : h("span", { class: "so-collection-empty", "aria-hidden": "true" }, Aira.icon("soBookmark")),
          h("span", { class: "so-collection-name" }, h("strong", { text: item.name }), h("span", { class: "small", text: count(item.count, "post", "posts") }))))),
        ...kids(me.age_group === "child" ? null : h("p", { class: "muted small" }, h("span", { text: "Things you saved on the Marketplace are under " }), h("a", { href: "#/social?tab=market&view=saved", text: "Marketplace › Saved" }), h("span", { text: "." }))));
      if (!data.posts.length) body.append(S.section("", Aira.ui.empty("Nothing saved yet", "Tap Save under a post to keep it here, in a collection of your own.", S.button("Explore", () => go({ tab: "explore" }), { kind: "secondary sm" }))));
      return;
    }
    const shelf = data.collections.find((item) => (item.id || "all") === wanted) || { id: "", name: "All saved" };
    body.append(h("div", { class: "so-section-head" }, S.button("All collections", () => go({ tab: "saved" }), { kind: "ghost sm", icon: "soLeft" }), h("h2", { class: "suite-h2", text: shelf.name }),
      shelf.id ? h("div", { class: "btn-row" },
        S.button("Rename", () => S.edit({ title: "Rename the collection", fields: [{ name: "name", label: "Name", required: true }], values: shelf, onSave: async (values) => { await api(`/collections/${shelf.id}`, { method: "PATCH", body: values }); Aira.router.render(); } }), { kind: "ghost sm" }),
        S.button("Delete", async () => { if (await Aira.confirm({ title: `Delete ${shelf.name}?`, message: "Its posts stay under All saved.", confirmLabel: "Delete collection", danger: true })) { await api(`/collections/${shelf.id}`, { method: "DELETE" }); go({ tab: "saved" }); } }, { kind: "ghost sm" })) : null));
    const { state, ctx } = postList(body, me, async () => (await api(`/saved${shelf.id ? `?collection=${shelf.id}` : ""}`)).posts, { empty: Aira.ui.empty("Nothing here yet", "Save posts into this collection with Save under a post.") });
    state.posts = data.posts; ctx.redraw();
  }

  // ---------------------------------------------------------------------------
  // Notifications and moderation
  // ---------------------------------------------------------------------------
  const KIND_BADGE = { reaction: "❤️", comment: "💬", reply: "↩️", comment_like: "👍", mention: "@", follow: "➕", friend_request: "🤝", friend_accepted: "🤝", share: "🔁", poll_vote: "📊", poll_result: "📊", story_reply: "💬", story_reaction: "😍", chat_added: "👥", group_request: "👥", group_joined: "👥", guardian_request: "🛡️", guardian_answer: "🛡️", market_interest: "🏷️" };
  const NOTE_FILTERS = [
    ["all", "All", () => true],
    ["unread", "Unread", (item) => !item.read],
    ["mentions", "Mentions", (item) => item.kind === "mention"],
    ["comments", "Comments", (item) => ["comment", "reply", "comment_like"].includes(item.kind)],
    ["reactions", "Reactions", (item) => ["reaction", "share", "story_reaction"].includes(item.kind)],
    ["people", "Follows and requests", (item) => ["follow", "friend_request", "friend_accepted", "guardian_request", "guardian_answer", "group_request", "group_joined"].includes(item.kind)],
    ["polls", "Polls", (item) => item.kind.startsWith("poll_")],
    ["stories", "Stories", (item) => item.kind.startsWith("story_")],
  ];
  const notificationLink = (item) => {
    if (item.ref_type === "post") return `#/social?tab=feed&post=${item.ref_id}`;
    if (item.ref_type === "chat") return `#/social?tab=chats&chat=${item.ref_id}`;
    if (item.ref_type === "group") return `#/social?tab=groups&group=${item.ref_id}`;
    if (item.ref_type === "listing") return `#/social?tab=market&listing=${item.ref_id}`;
    if (item.ref_type === "story") return "#/social?tab=feed";
    if (item.kind === "friend_request" || item.ref_type === "guardian") return "#/social?tab=friends";
    return item.actor ? `#/social?tab=profile&person=${item.actor.id}` : "#/social";
  };
  const dayBucket = (iso) => {
    const age = (Date.now() - new Date(iso).getTime()) / 86400000;
    if (new Date(iso).toDateString() === new Date().toDateString()) return "Today";
    return age < 7 ? "This week" : "Earlier";
  };
  async function notificationsTab(body, me, route) {
    const data = await api("/notifications");
    const filter = NOTE_FILTERS.find(([id]) => id === route.query.show) || NOTE_FILTERS[0];
    const chips = h("div", { class: "so-filter-bar", role: "group", "aria-label": "Show" }, ...NOTE_FILTERS.map(([id, label]) => h("button", { type: "button", class: `so-chip-btn ${id === filter[0] ? "on" : ""}`.trim(), "aria-pressed": String(id === filter[0]), text: label, onclick: () => go(id === "all" ? { tab: "notifications" } : { tab: "notifications", show: id }) })));
    const markAll = data.unread ? S.button("Mark all as read", async () => { await api("/notifications/read", { method: "POST", body: {} }); Aira.router.render(); }, { kind: "ghost sm" }) : null;
    const shown = data.notifications.filter(filter[2]);
    const groups = new Map();
    for (const item of shown) { const key = dayBucket(item.created_at); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(item); }
    const row = (item) => h("li", { class: `so-note ${item.read ? "" : "unread"}`.trim() },
      h("a", { href: notificationLink(item), class: "so-note-link", onclick: () => { if (!item.read) api(`/notifications/${item.id}/read`, { method: "POST", body: {}, silent: true }).catch(() => {}); } },
        h("span", { class: "so-note-face" }, item.actor ? face(item.actor, 44) : h("span", { class: "so-face", style: { width: "44px", height: "44px", fontSize: "22px" }, text: "🔔" }), h("span", { class: "so-note-kind", "aria-hidden": "true", text: KIND_BADGE[item.kind] || "🔔" })),
        h("span", { class: "so-note-text" }, h("span", { text: item.text }), h("span", { class: "muted small", text: when(item.created_at) })),
        item.read ? null : h("span", { class: "so-note-dot", role: "img", "aria-label": "New" })));
    body.append(h("div", { class: "so-section-head" }, chips, markAll));
    if (!shown.length) {
      body.append(S.section("", Aira.ui.empty(filter[0] === "all" ? "No notifications" : "Nothing here", "When someone reacts, comments, replies, mentions you, follows you or answers a story, it shows here.", S.button("Find friends", () => go({ tab: "friends" }), { kind: "secondary sm" }))));
      return;
    }
    for (const [label, items] of groups) body.append(h("section", { class: "card so-notes" }, h("h2", { class: "suite-h2", text: label }), h("ul", { class: "so-note-list" }, ...items.map(row))));
  }

  // What was copied with a report when it was filed: the chat (and, for Dating, both profiles), for moderators only.
  const transcriptLines = (lines) => h("ol", { class: "muted small", style: { margin: "4px 0", paddingLeft: "18px" } }, ...(lines || []).slice(-30).map((m) => h("li", { text: `${when(m.at)} ${m.by ? `[${m.by}]` : ""} ${m.deleted ? "(deleted) " : ""}${m.hidden ? "(hidden) " : ""}${m.text || (m.photo ? "📷" : "")}` })));
  const evidenceNode = (item) => {
    if (item.withheld) return h("p", { class: "muted small", text: "Dating evidence is kept for Monosage's moderators." });
    const data = item.data || {};
    const profiles = data.profiles ? Object.entries(data.profiles).map(([side, p]) => p && p.dating_profile ? h("p", { class: "muted small", text: `[${side}] ${p.dating_profile.first_name}: ${p.dating_profile.bio || ""}` }) : null) : [];
    return h("details", { class: "so-evidence" }, h("summary", { text: item.kind === "dating" ? "Dating evidence (chat and profiles)" : "The chat around it" }),
      ...profiles,
      data.transcript ? transcriptLines(data.transcript) : null,
      data.live_chat ? transcriptLines(data.live_chat) : null,
      ...(data.ended_chats || []).map((chat) => h("div", {}, h("p", { class: "muted small", text: `A match that ended (${chat.reason}) ${when(chat.ended_at)}` }), transcriptLines(chat.transcript))));
  };

  async function moderationTab(body) {
    const draw = async () => {
      const data = await api("/moderation");
      const words = h("textarea", { rows: "3", "aria-label": "Blocked words" });
      words.value = data.words.join(", ");
      body.replaceChildren(
        S.section(`Reports to review (${data.reports.length})`, data.reports.length ? h("ul", { class: "life-lines" }, ...data.reports.map((item) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
          h("div", { style: { flex: "1 1 260px", minWidth: "0" } },
            h("span", { class: "life-line-text", text: `A ${REPORT_LABELS[item.target_type]}${item.author ? ` by ${item.author.name} (@${item.author.handle})` : ""}` }),
            item.preview ? h("span", { class: "life-line-sub", style: { whiteSpace: "pre-wrap" }, text: `“${item.preview}”` }) : null,
            h("span", { class: "muted life-line-sub", text: item.automatic ? `Held by the automatic check ${when(item.created_at)}: ${item.reason}` : `Reported by ${item.reporter ? item.reporter.name : "someone"} ${when(item.created_at)}: ${item.reason}` }),
            ...(item.evidence || []).map(evidenceNode)),
          item.target_type === "account" ? null : S.button("Hide it", async () => { await api(`/moderation/${item.id}`, { method: "POST", body: { action: "hide" } }); await draw(); }, { kind: "sm" }),
          item.can_suspend ? S.button("Suspend the person", async () => { if (await Aira.confirm({ title: "Suspend this person?", message: "Their posts, comments and stories disappear and they cannot post or message.", confirmLabel: "Suspend", danger: true })) { await api(`/moderation/${item.id}`, { method: "POST", body: { action: "suspend" } }); await draw(); } }, { kind: "ghost sm" }) : null,
          S.button(item.held ? "Deliver it" : "Nothing wrong", async () => { await api(`/moderation/${item.id}`, { method: "POST", body: { action: item.held ? "release" : "dismiss" } }); await draw(); }, { kind: "ghost sm" })))) : Aira.ui.empty("Nothing to review", "Reports from your household, or about your household's people, appear here.")),
        S.section("Blocked words", h("p", { class: "muted", text: "These words are hidden with stars in posts, comments and messages. Separate them with commas." }), words,
          h("div", { class: "btn-row" }, S.button("Save words", async () => { await api("/moderation/words", { method: "PUT", body: { words: words.value.split(",").map((w) => w.trim()).filter(Boolean) } }); Aira.toast("Saved.", "success"); }))));
    };
    await draw();
  }

  // ---------------------------------------------------------------------------
  // Search across the top: people, chats, groups, pages, listings and the family
  // ---------------------------------------------------------------------------
  // Lists are small: the chats and the family are fetched whole and filtered here, kept a few seconds so typing
  // does not ask again; people, groups, pages and listings come from the server's own searches.
  const remembered = new Map();
  const cached = async (key, ms, load) => {
    const hit = remembered.get(key);
    if (hit && Date.now() - hit.at < ms) return hit.value;
    const value = await load();
    remembered.set(key, { at: Date.now(), value });
    return value;
  };
  const norm = (text) => String(text || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  const NOTHING = { people: [], chats: [], groups: [], pages: [], listings: [], family: [], events: [], familyMe: "" };
  async function find(query) {
    const q = String(query || "").trim();
    const words = norm(q.replace(/^@/, ""));
    if (words.length < 2) return NOTHING;
    const quiet = (promise, fallback) => promise.catch(() => fallback);
    const [found, chats, market, family] = await Promise.all([
      quiet(cached(`search:${q}`, 5000, () => api(`/search?q=${encodeURIComponent(q)}`, { silent: true })), { people: [], groups: [], pages: [] }),
      quiet(cached("chats", 8000, () => api("/chats", { silent: true })), { chats: [] }),
      ageGroup() === "child" ? { listings: [] } : quiet(cached(`market:${q}`, 5000, () => api(`/market?q=${encodeURIComponent(q)}`, { silent: true })), { listings: [] }),
      familyOpen() ? quiet(cached("family", 30000, async () => {
        const [overview, events] = await Promise.all([S.api("family", "/overview", { silent: true }), S.api("family", "/events", { silent: true })]);
        return { me: overview.me, people: overview.people, events: events.events };
      }), null) : null,
    ]);
    return {
      people: found.people || [],
      groups: found.groups || [],
      pages: found.pages || [],
      chats: (chats.chats || []).filter((chat) => norm(chat.title).includes(words) || (chat.last && !chat.last.deleted && norm(chat.last.text).includes(words))),
      listings: market.listings || [],
      family: family ? family.people.filter((person) => person.id !== family.me.id && norm(person.name).includes(words)) : [],
      events: family ? family.events.filter((event) => norm(`${event.title} ${event.place || ""}`).includes(words)) : [],
      familyMe: family ? family.me.id : "",
    };
  }
  const RELATION_TEXT = { friends: "Friend", request_received: "Wants to be friends", request_sent: "Request sent", awaiting_parent: "Waiting for a grown-up" };
  const eventDay = (event) => new Date(event.all_day ? `${String(event.starts_at).slice(0, 10)}T00:00:00` : event.starts_at).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  const familyChat = async (person) => {
    const created = await S.api("family", "/rooms/direct", { method: "POST", body: { profile_id: person.id } });
    Aira.router.go(["family"], { tab: "chat", room: created.room.id });
  };
  // One row per record for the list under the search box, each opening its record.
  const HITS = {
    people: (person) => ({ label: person.name, sub: [`@${person.handle}`, RELATION_TEXT[person.relation] || "Person"].join(" · "), icon: "soPerson", href: `#/social?tab=profile&person=${person.id}` }),
    chats: (chat) => ({ label: chat.title, sub: `Chat${chat.last && !chat.last.deleted ? ` · ${chat.last.mine ? "You: " : ""}${chat.last.text}` : ""}`, icon: "social", href: `#/social?tab=chats&chat=${chat.id}` }),
    groups: (group) => ({ label: group.name, sub: `Group · ${group.visibility === "public" ? "Public" : "Private"}`, icon: "soGroup", href: `#/social?tab=groups&group=${group.id}` }),
    pages: (page) => ({ label: page.name, sub: `Page${page.category ? ` · ${page.category}` : ""}`, icon: "soFlag", href: `#/social?tab=pages&page=${page.id}` }),
    listings: (listing) => ({ label: listing.title, sub: ["Marketplace", listing.price, listing.place].filter(Boolean).join(" · "), icon: "soTag", href: `#/social?tab=market&listing=${listing.id}` }),
    family: (person) => ({ label: person.name, sub: "Family · send a message", icon: "family", run: () => familyChat(person) }),
    events: (event) => ({ label: event.title, sub: `Family calendar · ${eventDay(event)}`, icon: "interviews", href: `#/family?tab=calendar&event=${event.id}` }),
  };
  const LANES = Object.keys(HITS);

  // Enter in the search box: everything that matches, by kind, each opening its record.
  async function searchTab(body, me, route) {
    const q = String(route.query.q || "").trim();
    if (q.length < 2) { body.append(Aira.ui.empty("Type what you are looking for", "In the search box at the top: a name, an @name, a group, a page or something for sale.")); return; }
    const found = await find(q);
    const sections = [];
    const add = (title, items, node) => { if (items.length) sections.push(S.section(`${title} (${items.length})`, node)); };
    const rerun = () => Aira.router.render();
    add("People", found.people, h("ul", { class: "life-people" }, ...found.people.map((person) => personRow(person, relationButtons(person, person.relation, rerun)))));
    const line = (hit, extra) => h("li", { class: "life-line" }, h("span", { class: "so-hit-icon", "aria-hidden": "true" }, Aira.icon(hit.icon)),
      h("div", { style: { flex: "1", minWidth: "0" } }, hit.href ? h("a", { href: hit.href, class: "life-line-text", text: hit.label }) : h("button", { type: "button", class: "so-linkish life-line-text", text: hit.label, onclick: hit.run }), h("span", { class: "muted life-line-sub", text: hit.sub })), extra || null);
    add("Chats", found.chats, h("ul", { class: "life-lines" }, ...found.chats.map((chat) => line(HITS.chats(chat)))));
    add("Groups", found.groups, h("ul", { class: "life-lines" }, ...found.groups.map((group) => line(HITS.groups(group)))));
    add("Pages", found.pages, h("ul", { class: "life-lines" }, ...found.pages.map((page) => line(HITS.pages(page)))));
    add("Marketplace", found.listings, listingGrid(found.listings, null));
    add("Family", found.family, h("ul", { class: "life-lines" }, ...found.family.map((person) => line(HITS.family(person)))));
    add("Family calendar", found.events, h("ul", { class: "life-lines" }, ...found.events.map((event) => line(HITS.events(event)))));
    if (!sections.length) {
      body.append(S.section("", Aira.ui.empty(`Nothing in Social matches “${q}”`, "Try part of a name, or ask them for their @name.",
        h("div", { class: "btn-row", style: { justifyContent: "center" } }, S.button("Find people", () => go({ tab: "friends", q }), { kind: "secondary sm" }), ageGroup() === "child" ? null : S.button("Search the Marketplace", () => go({ tab: "market", q }), { kind: "secondary sm" })))));
      return;
    }
    body.append(...sections);
  }

  // ---------------------------------------------------------------------------
  // First visit: choose a Social name
  // ---------------------------------------------------------------------------
  function setup(main, info) {
    const young = info.age_group === "child" || info.age_group === "teen";
    const body = S.page(main, { title: "Social", subtitle: "Chat with friends and family, share photos and news." });
    const form = S.form([
      { name: "display_name", label: "Your name", required: true },
      { name: "handle", label: "Your @name (people find you by it)", required: true, hint: "3 to 30 letters, numbers, dots or underscores." },
      { name: "avatar", label: "Picture: an emoji (you can upload a photo later)" },
      { name: "bio", label: "About you (optional)", type: "textarea", rows: 2 },
      young ? null : { name: "visibility", label: "Who can see your profile", type: "select", options: [["public", "Everyone"], ["private", "Only my friends"]] },
    ].filter(Boolean), { display_name: info.name, handle: info.suggested_handle, avatar: info.avatar, visibility: "public" });
    body.append(S.section("Join Social", h("p", { class: "muted", text: young ? "Your profile stays private: only friends see it and only friends can message you." : "You can change all of this later." }), form.node,
      h("div", { class: "btn-row" }, S.button("Join Social", async () => {
        try { await api("/me", { method: "POST", body: form.read() }); Aira.router.render(); } catch (error) { form.setError(error.message); }
      }))));
  }

  // ---------------------------------------------------------------------------
  // "+ Create": one place to make anything; "+ New" in the frame asks for the same things
  // ---------------------------------------------------------------------------
  const createItems = (me) => kids(
    { id: "post", label: "Post", about: "Words, photos and a feeling", icon: "soPen" },
    { id: "photo", label: "Photo", about: "From your phone or computer", icon: "soPhoto" },
    { id: "story", label: "Story", about: "For your friends, 24 hours", icon: "soStory" },
    { id: "reel", label: "Reel", about: "A tall short video, in Videos", icon: "soReel" },
    { id: "poll", label: "Poll", about: "Ask, and see what people think", icon: "soPoll" },
    me.age_group === "child" ? null : { id: "group", label: "Group", about: "For a club, a street, a hobby", icon: "soGroup" },
    young(me) ? null : { id: "page", label: "Page", about: "For a shop, club or service", icon: "soFlag" },
    young(me) ? null : { id: "listing", label: "Listing", about: "Sell on the Marketplace", icon: "soTag" },
  );
  function openCreate(me) {
    let dialog = null;
    const grid = h("div", { class: "so-create-grid" }, ...createItems(me).map((item) => h("button", { type: "button", class: "so-create-item", onclick: () => { dialog.close(); startCreate(item.id, me); } },
      h("span", { class: "so-create-icon", "aria-hidden": "true" }, Aira.icon(item.icon)), h("strong", { text: item.label }), h("span", { class: "muted small", text: item.about }))));
    dialog = Aira.modal({ title: "Create", body: grid, actions: [{ label: "Close", kind: "ghost" }] });
  }
  // Open the form for something new: on its own section when it lives there (the composer is on Home).
  function startCreate(kind, me) {
    const here = Aira.router.current && Aira.router.current.query ? Aira.router.current.query.tab || "feed" : "";
    if (kind === "reel") { Aira.router.go(["videos"], { tab: "studio", new: "reel" }); return; }
    const tab = NEW_TAB[kind] || "feed";
    if ((tab === "feed" && (here === "feed" || here === "")) || (tab !== "feed" && here === tab)) { fulfil(kind, me, document.getElementById("main")); return; }
    go({ tab, new: kind });
  }
  const createButton = (me) => h("button", { type: "button", class: "btn primary so-create-btn", "aria-haspopup": "dialog", onclick: () => openCreate(me) }, Aira.icon("soPlus"), h("span", { text: "Create" }));

  // "+ New" asked for something: open the same form as the button on its tab, if this person may make it.
  function fulfil(ask, me, main) {
    const opened = (() => {
      const box = main.querySelector("[data-so-composer]");
      const card = box ? box.closest(".so-composer") : null;
      if (ask === "post" || ask === "photo" || ask === "poll") {
        if (box) { box.scrollIntoView({ block: "center" }); box.focus(); }
        if (card && ask === "photo") card.openPhotos();
        if (card && ask === "poll") card.openPoll();
        return null;
      }
      if (ask === "story") { const strip = main.querySelector(".so-stories"); return addStory(strip && strip.redraw ? strip.redraw : async () => {}); }
      if (ask === "chat") return newChat();
      if (ask === "group" && me.age_group !== "child") return startGroup();
      if (ask === "page" && !young(me)) return createPage();
      if (ask === "listing" && !young(me)) return newListing();
      return null;
    })();
    if (opened && opened.catch) opened.catch((error) => Aira.toast(error.message, "error"));
  }
  const NEW_TAB = { post: "feed", photo: "feed", poll: "feed", story: "feed", chat: "chats", group: "groups", page: "pages", listing: "market" };

  // ---------------------------------------------------------------------------
  // The phone's bottom bar: the five most used sections and More (the rest, and Monosage's own menu)
  // ---------------------------------------------------------------------------
  function bottomBar(tabs, current, me) {
    const shown = BOTTOM.map((id) => tabs.find((item) => item.id === id)).filter(Boolean);
    const rest = tabs.filter((item) => !BOTTOM.includes(item.id));
    const link = (item) => h("a", { href: `#/social?tab=${item.id}`, class: "so-bottom-item", "aria-label": item.label, "aria-current": item.id === current ? "page" : "false" }, Aira.icon(item.icon), h("span", { "aria-hidden": "true", text: BOTTOM_SHORT[item.id] || item.label }), item.count ? h("span", { class: "so-bottom-count", "aria-label": `${item.count} new`, text: item.count > 99 ? "99+" : String(item.count) }) : null);
    const restCount = rest.reduce((sum, item) => sum + (item.count || 0), 0);
    const more = h("button", { type: "button", class: "so-bottom-item", "aria-haspopup": "dialog", "aria-current": rest.some((item) => item.id === current) ? "page" : "false", onclick: () => {
      let dialog = null;
      const list = h("div", { class: "so-more-grid" }, ...rest.map((item) => h("a", { href: `#/social?tab=${item.id}`, class: `so-more-item ${item.id === current ? "on" : ""}`.trim(), onclick: () => dialog.close() }, Aira.icon(item.icon), h("span", { text: item.label }), item.count ? h("span", { class: "so-bottom-count", text: String(item.count) }) : null)));
      dialog = Aira.modal({ title: "More in Social", body: h("div", { class: "suite-body" }, h("a", { href: "#/social?tab=profile", class: "so-more-me", onclick: () => dialog.close() }, face(me, 44), h("span", {}, h("strong", { text: me.display_name }), h("span", { class: "muted small", text: "See your profile" }))), list), actions: [{ label: "Monosage menu", kind: "ghost", onClick: () => { if (Aira.setSidebarOpen) Aira.setSidebarOpen(true); } }, { label: "Close", kind: "secondary" }] });
      dialog.box.classList.add("so-sheet");
    } }, Aira.icon("soDots"), h("span", { text: "More" }), restCount ? h("span", { class: "so-bottom-count", text: String(restCount) }) : null);
    return h("nav", { class: "so-bottom", "aria-label": "Social sections" }, ...shown.map(link), more);
  }

  Aira.views.social = async (main, route) => {
    // "+ New" asks with ?new=<kind>: remember it, take it out of the address (Back or a refresh must not ask again)
    // and draw the page; the drawn page then opens the form.
    if (route.query.new) {
      pending = String(route.query.new);
      const rest = { ...route.query };
      delete rest.new;
      if (!rest.tab) rest.tab = NEW_TAB[pending] || "";
      Aira.router.go(["social"], rest, { replace: true });
      return;
    }
    let info;
    try { info = await api("/me"); } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    if (!info.account) { pending = ""; setup(main, info); return; }
    const me = info.account;
    known = me;
    const unread = info.badge || { messages: 0, notifications: 0 };
    const tabs = [
      { id: "feed" },
      { id: "explore" },
      { id: "reels" },
      { id: "chats", count: unread.messages },
      { id: "friends" },
      Aira.socialDating && Aira.socialDating.available(me.age_group) ? { id: "dating" } : null,
      { id: "groups" },
      { id: "pages" },
      me.age_group === "child" ? null : { id: "market" },
      { id: "notifications", count: unread.notifications },
      { id: "saved" },
      { id: "profile" },
      info.moderator ? { id: "moderation" } : null,
    ].filter(Boolean).map((item) => ({ ...item, label: TAB_META[item.id].label, icon: TAB_META[item.id].icon }));
    const searching = route.query.tab === "search";
    const tabId = searching ? "search" : tabs.some((item) => item.id === route.query.tab) ? route.query.tab : "feed";
    const query = String(route.query.q || "").trim();
    // The page is named after the open section (the app's name is in the bar above), like a mail app's folder.
    const news = [unread.messages ? count(unread.messages, "new message", "new messages") : "", unread.notifications ? count(unread.notifications, "notification", "notifications") : ""].filter(Boolean);
    const title = searching ? (query ? `Results for “${query}”` : "Search") : TAB_META[tabId].label;
    const subtitle = searching ? "People, chats, groups, pages, things for sale and your family." : tabId === "feed" && news.length ? news.join(" · ") : TAB_META[tabId].about;
    const body = S.page(main, { title, subtitle, actions: [createButton(me)] });
    body.classList.add("so-page", `so-on-${tabId}`);
    document.title = `${title} · Social · Monosage`;
    // The menu's folders (shell.js draws them from these items: the rail on a wide screen) carry the counts.
    S.tabs(body, route, "social", tabs.map((item) => ({ id: item.id, icon: item.icon, label: item.label, count: item.count || 0 })), searching ? "" : tabId);
    const wide = ["chats", "market", "explore", "reels", "moderation", "dating"].includes(tabId);
    const panel = h("div", { class: `suite-body ${wide ? "" : "so-column"} ${tabId === "feed" && !route.query.post && !route.query.tag ? "so-column-wide" : ""}`.trim() });
    body.append(panel);
    // on a phone: the bottom bar stands in for the page's tab bar and the frame's own bar
    const bar = bottomBar(tabs, searching ? "" : tabId, me);
    body.append(bar);
    try {
      if (tabId === "explore") await exploreTab(panel, me, route);
      else if (tabId === "reels") await reelsTab(panel, me, route);
      else if (tabId === "chats") await chatsTab(panel, me, route);
      else if (tabId === "friends") await friendsTab(panel, me, route);
      else if (tabId === "groups") await groupsTab(panel, me, route);
      else if (tabId === "pages") await pagesTab(panel, me, route);
      else if (tabId === "market") await marketTab(panel, me, route);
      else if (tabId === "dating") await Aira.socialDating.tab(panel, route, () => Aira.router.render());
      else if (tabId === "profile") await profileTab(panel, me, route);
      else if (tabId === "saved") await savedTab(panel, me, route);
      else if (tabId === "notifications") await notificationsTab(panel, me, route);
      else if (tabId === "moderation") await moderationTab(panel);
      else if (tabId === "search") await searchTab(panel, me, route);
      else await feedTab(panel, me, route);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); }
    const ask = pending;
    pending = "";
    if (ask && main.isConnected) fulfil(ask, me, main);
  };

  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  // "+ New": what people make most here, each opening the same form as "+ Create". Children have no Marketplace and
  // make no groups; children and teens do not sell or make pages.
  Aira.appNew.social = () => {
    const age = ageGroup();
    const isYoung = age === "child" || age === "teen";
    return [
      { label: "Post", icon: "soPen", href: "#/social?tab=feed&new=post" },
      { label: "Photo", icon: "soPhoto", href: "#/social?tab=feed&new=photo" },
      { label: "Story", icon: "soStory", href: "#/social?tab=feed&new=story" },
      { label: "Reel", icon: "soReel", href: "#/videos?tab=studio&new=reel" },
      { label: "Poll", icon: "soPoll", href: "#/social?tab=feed&new=poll" },
      { label: "Chat", icon: "social", href: "#/social?tab=chats&new=chat" },
      age === "child" ? null : { label: "Group", icon: "soGroup", href: "#/social?tab=groups&new=group" },
      isYoung ? null : { label: "Page", icon: "soFlag", href: "#/social?tab=pages&new=page" },
      isYoung ? null : { label: "Marketplace listing", icon: "soTag", href: "#/social?tab=market&new=listing" },
      familyOpen() ? { label: "Family event", icon: "interviews", href: "#/family?tab=calendar&new=event" } : null,
    ].filter(Boolean);
  };
  Aira.appSearch.social = {
    placeholder: "Search people, chats, groups and the Marketplace",
    // Up to six records, one of each kind in turn so one busy kind does not hide the others. A #tag lists its posts.
    async suggest(query) {
      const q = String(query || "").trim();
      if (/^#[\p{L}\p{N}_]{2,40}$/u.test(q)) return [{ label: `Posts tagged ${q}`, sub: "Home", icon: "news", href: `#/social?tab=feed&tag=${encodeURIComponent(q.slice(1).toLowerCase())}` }];
      const found = await find(q);
      const out = [];
      for (let round = 0; out.length < 6 && LANES.some((lane) => found[lane].length > round); round += 1) {
        for (const lane of LANES) if (found[lane][round] && out.length < 6) out.push(HITS[lane](found[lane][round]));
      }
      return out;
    },
    run(query) {
      const q = String(query || "").trim();
      if (/^#[\p{L}\p{N}_]{2,40}$/u.test(q)) go({ tab: "feed", tag: q.slice(1).toLowerCase() });
      else go({ tab: "search", q });
    },
  };
  // What Dating and other sections may reuse.
  Aira.socialKit = { api, go, face, when, personLink, personRow, relationButtons, report, block, message, menuButton, uploadPhoto, photoPicker, carousel, choosePhotos, count, kids, live };
})();

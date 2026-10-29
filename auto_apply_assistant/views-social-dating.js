/* Aira — Dating in Social: meet new people, adults only, opt-in, with a profile separate from the Social one.
 *
 * Social's "Dating" section draws it with Aira.socialDating.tab(holder, route, render). The section keeps its own
 * place in the address: ?tab=<the section's tab>&dv=discover|likes|matches|profile|safety (&mode=likes, &match=<id>).
 *   - No dating profile yet: a welcome page and a gentle four-step setup (about you, photos, prompts, who you'd like
 *     to meet).
 *   - Discover: one big card at a time (photos, name, age, area, prompts, interests in common), Pass, Star and Like
 *     (buttons, arrow keys or dragging the card), Undo the last pass, filters, and a celebration on a match.
 *   - Likes you: how many, with a hint of each (an initial and what you share); "Meet them" shows them in Discover.
 *   - Matches: new matches and conversations; a match's chat (Social's chat system) with icebreakers, and
 *     unmatch, block and report.
 *   - Profile: how others see you, edit, pause, privacy, download and delete. Safety: tips.
 * Everything runs through /api/social/dating/* (aira/suite/social/dating_routes.py). Children and teens never see
 * it (Aira.socialDating.available) and the server refuses them. DOM is built with h() and textContent only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("social", `/dating${path}`, opts);

  const INTERESTS = {
    hiking: "🥾 Hiking", cooking: "🍳 Cooking", baking: "🧁 Baking", music: "🎵 Music", concerts: "🎤 Live music", films: "🎬 Films",
    reading: "📚 Reading", writing: "✍️ Writing", travel: "✈️ Travel", photography: "📷 Photography", art: "🎨 Art", gaming: "🎮 Gaming",
    board_games: "🎲 Board games", fitness: "🏋️ Fitness", yoga: "🧘 Yoga", running: "🏃 Running", cycling: "🚲 Cycling", swimming: "🏊 Swimming",
    dancing: "💃 Dancing", coffee: "☕ Coffee", food: "🍜 Food", gardening: "🌿 Gardening", dogs: "🐶 Dogs", cats: "🐱 Cats",
    volunteering: "🤝 Volunteering", technology: "💻 Technology", theatre: "🎭 Theatre", football: "⚽ Football", camping: "⛺ Camping",
    languages: "🗣️ Languages", fashion: "👗 Fashion", podcasts: "🎧 Podcasts", comedy: "😂 Comedy", science: "🔬 Science", faith: "🕊️ Faith", family: "🏡 Family",
  };
  const GENDERS = { woman: "Woman", man: "Man", nonbinary: "Non-binary" };
  const SHOW_ME = { women: "Women", men: "Men", everyone: "Everyone" };
  const LOOKING_FOR = { relationship: "A relationship", casual: "Something casual", friendship: "New friends", unsure: "Not sure yet" };
  const LOOKING_ICON = { relationship: "💞", casual: "☕", friendship: "🤝", unsure: "🌱" };
  const interestLabel = (key) => INTERESTS[key] || key;
  const plainInterest = (key) => interestLabel(key).replace(/^\S+\s/, "");

  const GLYPHS = {
    sdHeart: '<svg viewBox="0 0 24 24"><path d="M12 20.5s-7.5-4.6-9.2-9.4C1.6 7.6 3.9 4.5 7.2 4.5c2 0 3.5 1.1 4.8 2.9 1.3-1.8 2.8-2.9 4.8-2.9 3.3 0 5.6 3.1 4.4 6.6-1.7 4.8-9.2 9.4-9.2 9.4z"/></svg>',
    sdClose: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    sdStar: '<svg viewBox="0 0 24 24"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/></svg>',
    sdUndo: '<svg viewBox="0 0 24 24"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>',
    sdChat: '<svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/></svg>',
    sdSpark: '<svg viewBox="0 0 24 24"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/></svg>',
    sdShield: '<svg viewBox="0 0 24 24"><path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>',
    sdUser: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    sdSliders: '<svg viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
    sdPin: '<svg viewBox="0 0 24 24"><path d="M12 21s-6-5.6-6-11a6 6 0 0 1 12 0c0 5.4-6 11-6 11z"/><circle cx="12" cy="10" r="2.2"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;
  const glyph = (name) => h("span", { class: "sd-glyph", "aria-hidden": "true", html: GLYPHS[name] });

  const adultGroup = (group) => group === "adult" || group === "senior";
  const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const when = (iso) => {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const minutes = Math.round((Date.now() - date.getTime()) / 60000);
    if (minutes < 1) return "Just now";
    if (minutes < 60) return `${minutes} min`;
    if (date.toDateString() === new Date().toDateString()) return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return date.toLocaleDateString([], { month: "short", day: "numeric" });
  };
  const tint = (id) => (parseInt(String(id || "0").slice(0, 6), 16) || 0) % 360;
  // Custom properties need setProperty (a style object would drop them).
  const vars = (node, values) => { for (const [name, value] of Object.entries(values)) node.style.setProperty(name, String(value)); return node; };
  const present = (...nodes) => nodes.filter((node) => node !== null && node !== undefined && node !== false);
  const safePhoto = (src) => /^https:\/\//i.test(src) || /^\/api\/social\/photos\//.test(src);

  // A face for lists and the chat: the first photo, or the initial on a soft colour.
  const face = (profile, size = 48) => {
    const photo = profile && (profile.photos || []).find(safePhoto);
    const box = { width: `${size}px`, height: `${size}px` };
    if (photo) return h("img", { class: "sd-face", src: photo, alt: "", loading: "lazy", referrerpolicy: "no-referrer", style: box });
    return vars(h("span", { class: "sd-face sd-face-letter", "aria-hidden": "true", style: { ...box, fontSize: `${Math.round(size * 0.42)}px` }, text: ((profile && profile.first_name) || "?").slice(0, 1).toUpperCase() }), { "--sd-tint": tint(profile && profile.id) });
  };

  // ---------------------------------------------------------------------------
  // The card: what other people see of a dating profile
  // ---------------------------------------------------------------------------
  function profileCard(card, { preview = false, onReport = null } = {}) {
    const photos = (card.photos || []).filter(safePhoto);
    let index = 0;
    const photoArea = vars(h("div", { class: "sd-photo" }), { "--sd-tint": tint(card.id) });
    const dots = h("div", { class: "sd-dots", "aria-hidden": "true" });
    const drawPhoto = () => {
      const img = photos.length
        ? h("img", { src: photos[index], alt: `Photo ${index + 1} of ${photos.length} of ${card.first_name}`, referrerpolicy: "no-referrer", draggable: "false" })
        : h("span", { class: "sd-photo-letter", "aria-hidden": "true", text: (card.first_name || "?").slice(0, 1).toUpperCase() });
      dots.replaceChildren(...photos.map((_, i) => h("span", { class: i === index ? "on" : "" })));
      photoArea.replaceChildren(...present(img, photos.length > 1 ? dots : null,
        photos.length > 1 ? h("button", { type: "button", class: "sd-photo-prev", "aria-label": "Previous photo", onclick: (event) => { event.stopPropagation(); index = (index - 1 + photos.length) % photos.length; drawPhoto(); } }) : null,
        photos.length > 1 ? h("button", { type: "button", class: "sd-photo-next", "aria-label": "Next photo", onclick: (event) => { event.stopPropagation(); index = (index + 1) % photos.length; drawPhoto(); } }) : null,
        card.starred_you ? h("span", { class: "sd-ribbon sd-ribbon-star" }, glyph("sdStar"), h("span", { text: "Starred you" })) : card.likes_you ? h("span", { class: "sd-ribbon" }, glyph("sdHeart"), h("span", { text: "Likes you" })) : null,
        h("div", { class: "sd-photo-info" },
          h("h2", { class: "sd-name" }, h("span", { text: card.first_name }), card.age ? h("span", { class: "sd-age", text: String(card.age) }) : null),
          card.city ? h("p", { class: "sd-place" }, glyph("sdPin"), h("span", { text: card.city })) : null)));
    };
    drawPhoto();
    const shared = new Set(card.shared || []);
    const facts = [
      card.looking_for ? h("span", { class: "sd-fact" }, h("span", { "aria-hidden": "true", text: LOOKING_ICON[card.looking_for] || "" }), h("span", { text: LOOKING_FOR[card.looking_for] || "" })) : null,
      card.gender ? h("span", { class: "sd-fact", text: GENDERS[card.gender] || "" }) : null,
      card.height_cm ? h("span", { class: "sd-fact", text: `${card.height_cm} cm` }) : null,
    ].filter(Boolean);
    const details = h("div", { class: "sd-details" },
      facts.length ? h("div", { class: "sd-facts" }, ...facts) : null,
      card.bio ? h("p", { class: "sd-bio", text: card.bio }) : null,
      ...(card.prompts || []).map((prompt) => h("figure", { class: "sd-prompt" }, h("figcaption", { text: prompt.question }), h("blockquote", { text: prompt.answer }))),
      (card.interests || []).length ? h("div", { class: "sd-interests-wrap" },
        shared.size && !preview ? h("p", { class: "sd-common", text: shared.size === 1 ? "1 interest in common" : `${shared.size} interests in common` }) : null,
        h("ul", { class: "sd-chips", "aria-label": "Interests" }, ...card.interests.map((key) => h("li", { class: `sd-chip ${shared.has(key) && !preview ? "sd-shared" : ""}`.trim() }, h("span", { text: interestLabel(key) }), shared.has(key) && !preview ? h("span", { class: "sr-only", text: " (in common)" }) : null)))) : null,
      onReport ? h("div", { class: "sd-card-foot" }, h("button", { type: "button", class: "btn ghost sm", onclick: onReport }, glyph("sdShield"), h("span", { text: `Report or block ${card.first_name}` }))) : null);
    return h("article", { class: "sd-card", "aria-label": card.age ? `${card.first_name}, ${card.age}` : card.first_name }, photoArea, details);
  }

  // ---------------------------------------------------------------------------
  // Safety: report, block, tips
  // ---------------------------------------------------------------------------
  const reportProfile = (card, after) => S.edit({
    title: `Report ${card.first_name}`,
    fields: [
      { name: "reason", label: "What is wrong?", type: "textarea", rows: 3, placeholder: "Fake photos, pressure, asking for money, threats, someone under 18…" },
      { name: "block", label: `Also block ${card.first_name} (everywhere in Social)`, type: "checkbox" },
    ],
    values: { block: true },
    submitLabel: "Send report",
    onSave: async (values) => {
      await api(`/profiles/${card.id}/report`, { method: "POST", body: values });
      Aira.toast("Thank you. The moderators will review your report. You will not see this profile again.", "success");
      if (after) await after();
    },
  });

  const SAFETY = [
    { icon: "💬", title: "Chat here first", text: "Keep the conversation in Monosage until you feel sure. Your phone number, address, work and other accounts can wait." },
    { icon: "☕", title: "Meet in a public place", text: "Choose a busy café or park for the first meetings, tell a friend where you will be and when, and arrange your own way there and back." },
    { icon: "🚫", title: "Never send money", text: "Nobody who is right for you asks for money, gift cards, crypto or help with an investment. Report it straight away." },
    { icon: "🧭", title: "Trust your instincts", text: "You can unmatch or block at any time and nobody is told why. Blocking someone also blocks them everywhere in Social." },
    { icon: "🛡️", title: "Report what feels wrong", text: "Fake photos, pressure, threats or someone who seems to be under 18: report the profile. The moderators review every report." },
    { icon: "🔒", title: "Your Social profile stays separate", text: "People in Dating see only your dating profile: your first name, age, area, photos and what you chose to share. Nothing about Dating appears on your Social profile or feed." },
  ];
  function safetyView(body) {
    body.append(h("section", { class: "card suite-section sd-safety" },
      h("div", { class: "sd-safety-head" }, glyph("sdShield"), h("div", {}, h("h2", { class: "suite-h2", text: "Date safely" }), h("p", { class: "muted", text: "Dating is only for adults (18 and over). A few habits keep it fun and safe." }))),
      h("ul", { class: "sd-tips" }, ...SAFETY.map((tip) => h("li", { class: "sd-tip" }, h("span", { class: "sd-tip-icon", "aria-hidden": "true", text: tip.icon }), h("div", {}, h("strong", { text: tip.title }), h("p", { text: tip.text }))))),
      h("p", { class: "sd-emergency", text: "If you are ever in danger, call your local emergency number." })));
  }

  // ---------------------------------------------------------------------------
  // Setup and editing: four gentle steps
  // ---------------------------------------------------------------------------
  const STEPS = [
    { title: "About you", hint: "Only your first name and age are shown, never your Social name." },
    { title: "Photos and a few words", hint: "Clear, recent photos of you, on your own. Add up to six." },
    { title: "Three prompts", hint: "Answers people can reply to. Be yourself." },
    { title: "Who you'd like to meet", hint: "You can change all of this later." },
  ];
  const adultDate = () => { const day = new Date(); day.setFullYear(day.getFullYear() - 18); return day.toISOString().slice(0, 10); };

  function chips(options, selected, { multi = false, max = 0, onChange, label }) {
    const group = h("div", { class: "sd-choice", role: "group", "aria-label": label });
    const draw = () => group.replaceChildren(...Object.entries(options).map(([key, text]) => {
      const on = multi ? selected.has(key) : selected.value === key;
      return h("button", { type: "button", class: `sd-option ${on ? "on" : ""}`.trim(), "aria-pressed": String(on), "data-value": key, text, onclick: () => {
        if (multi) {
          if (on) selected.delete(key);
          else if (max && selected.size >= max) { Aira.toast(`Choose up to ${max}.`, "info"); return; }
          else selected.add(key);
        } else selected.value = key;
        draw();
        if (onChange) onChange();
      } });
    }));
    draw();
    return group;
  }
  const field = (label, control, hint) => Aira.ui.field(label, control, hint);
  const toggle = (label, hint, checked, onChange) => {
    const input = h("input", { type: "checkbox", class: "sd-switch-input", checked: !!checked, onchange: (event) => onChange(event.currentTarget.checked, event.currentTarget) });
    return h("label", { class: "sd-switch" }, input, h("span", { class: "sd-switch-track", "aria-hidden": "true" }), h("span", { class: "sd-switch-text" }, h("strong", { text: label }), hint ? h("span", { class: "muted small", text: hint }) : null));
  };

  function wizard(body, info, onDone, startStep = 0) {
    const existing = info.profile;
    const options = info.options;
    const prompts = Object.fromEntries(options.prompts.map((p) => [p.id, p.question]));
    const draft = existing ? {
      first_name: existing.first_name, gender: existing.gender, city: existing.city, height_cm: existing.height_cm || "", bio: existing.bio,
      photos: [...existing.photos], prompts: existing.prompts.map((p) => ({ id: p.id, answer: p.answer })), interests: new Set(existing.interests),
      looking_for: { value: existing.looking_for }, show_me: { value: existing.show_me }, gender_sel: { value: existing.gender },
      age_min: existing.age_min, age_max: existing.age_max, hide_friends: existing.hide_friends, hide_home: existing.hide_home, birthday: existing.birthday,
    } : {
      first_name: info.suggested.first_name || "", city: "", height_cm: "", bio: "", photos: [], prompts: [{ id: "", answer: "" }, { id: "", answer: "" }, { id: "", answer: "" }],
      interests: new Set(), looking_for: { value: "" }, show_me: { value: "" }, gender_sel: { value: "" }, age_min: "", age_max: "", hide_friends: false, hide_home: true, birthday: "", confirm_adult: false,
    };
    let step = startStep;
    const wrap = h("section", { class: "card suite-section sd-wizard", "aria-labelledby": "sdStepTitle" });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const fail = (message) => { error.textContent = message || ""; error.hidden = !message; };

    const stepAbout = () => {
      const name = h("input", { type: "text", maxlength: "30", value: draft.first_name, autocomplete: "given-name", oninput: (e) => { draft.first_name = e.currentTarget.value; } });
      const birthday = info.suggested.birthday_known ? null : h("input", { type: "date", max: adultDate(), value: draft.birthday, oninput: (e) => { draft.birthday = e.currentTarget.value; } });
      const city = h("input", { type: "text", maxlength: "60", value: draft.city, placeholder: "For example Riverside, or North London", autocomplete: "address-level2", oninput: (e) => { draft.city = e.currentTarget.value; } });
      const height = h("input", { type: "number", min: "120", max: "230", inputmode: "numeric", value: draft.height_cm, placeholder: "170", oninput: (e) => { draft.height_cm = e.currentTarget.value; } });
      return [
        field("Your first name", name),
        info.suggested.birthday_known ? h("p", { class: "sd-known" }, h("strong", { text: `Age ${info.suggested.age}` }), h("span", { class: "muted", text: " · from the birthday on your profile" })) : field("Your birthday", birthday, "Only your age is shown. You must be 18 or over."),
        h("div", { class: "field" }, h("span", { class: "sd-label", text: "I am a" }), chips(GENDERS, draft.gender_sel, { label: "I am a" })),
        field("Your city or area (optional)", city, "A town or neighbourhood, never your address."),
        field("Your height in cm (optional)", height),
        existing ? null : h("label", { class: "suite-check sd-confirm" }, h("input", { type: "checkbox", checked: !!draft.confirm_adult, onchange: (e) => { draft.confirm_adult = e.currentTarget.checked; fail(""); } }), h("span", { text: "I am 18 or over, and I will treat people with respect." })),
      ];
    };

    const stepPhotos = () => {
      const grid = h("div", { class: "sd-photo-grid" });
      const drawGrid = () => grid.replaceChildren(
        ...draft.photos.map((src, i) => h("figure", { class: "sd-thumb" },
          safePhoto(src) ? h("img", { src, alt: `Your photo ${i + 1}`, referrerpolicy: "no-referrer" }) : h("span", { class: "sd-thumb-empty", text: "📷" }),
          i === 0 ? h("figcaption", { text: "Main" }) : null,
          h("button", { type: "button", class: "sd-thumb-remove", "aria-label": `Remove photo ${i + 1}`, text: "✕", onclick: () => { draft.photos.splice(i, 1); drawGrid(); } }))),
        // empty places fill the row; each opens the upload (or the link box)
        ...Array.from({ length: draft.photos.length >= options.max_photos ? 0 : (3 - (draft.photos.length % 3)) % 3 || 3 }, () => h("button", { type: "button", class: "sd-thumb sd-thumb-slot", "aria-label": "Add a photo", onclick: () => (upload ? upload.click() : link.focus()) }, h("span", { "aria-hidden": "true", text: "+" }))));
      drawGrid();
      const link = h("input", { type: "url", placeholder: "https://…", "aria-label": "Photo link" });
      const addLink = () => {
        const value = link.value.trim();
        if (!value) { link.focus(); return; }
        if (!/^https:\/\//i.test(value)) { fail("Photo links must start with https://."); return; }
        if (draft.photos.length >= options.max_photos) { fail(`Add up to ${options.max_photos} photos.`); return; }
        fail("");
        draft.photos.push(value);
        link.value = "";
        drawGrid();
      };
      link.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); addLink(); } });
      // Photos from the device (Social's upload: shrunk in the browser, metadata taken out, kept as the person's own).
      const kit = Aira.socialKit;
      const canUpload = !!(kit && kit.uploadPhoto && kit.choosePhotos);
      const upload = canUpload ? S.button("Upload photos", async () => {
        const files = await kit.choosePhotos(true);
        const room = options.max_photos - draft.photos.length;
        if (files.length > room) Aira.toast(`Add up to ${options.max_photos} photos.`, "info");
        for (const file of files.slice(0, Math.max(0, room))) {
          try { draft.photos.push((await kit.uploadPhoto(file)).url); drawGrid(); }
          catch (err) { fail(err.message); }
        }
      }, { kind: "secondary", icon: "soPhoto" }) : null;
      const bio = h("textarea", { rows: "3", maxlength: "300", placeholder: "What makes you, you?", oninput: (e) => { draft.bio = e.currentTarget.value; counter.textContent = `${e.currentTarget.value.length}/300`; } });
      bio.value = draft.bio;
      const counter = h("span", { class: "muted small sd-counter", text: `${draft.bio.length}/300` });
      return [
        grid,
        h("div", { class: "sd-add-photo" }, upload, h("div", { class: "sd-grow" }, field(canUpload ? "Or add a photo link" : "Add a photo link", link)), S.button("Add", addLink, { kind: "secondary" })),
        h("p", { class: "muted small", text: "Profiles with at least two photos are liked far more often. The first photo is your main one." }),
        field("A few words about you (optional)", bio), counter,
      ];
    };

    const stepPrompts = () => draft.prompts.map((prompt, i) => {
      const select = h("select", { "aria-label": `Prompt ${i + 1}`, onchange: (e) => { prompt.id = e.currentTarget.value; } },
        h("option", { value: "", text: "Choose a prompt…" }),
        ...Object.entries(prompts).map(([id, question]) => h("option", { value: id, text: question, selected: prompt.id === id })));
      const answer = h("textarea", { rows: "2", maxlength: "200", "aria-label": `Your answer to prompt ${i + 1}`, placeholder: "Your answer", oninput: (e) => { prompt.answer = e.currentTarget.value; } });
      answer.value = prompt.answer;
      return h("div", { class: "sd-prompt-edit" }, h("span", { class: "sd-prompt-n", "aria-hidden": "true", text: String(i + 1) }), h("div", { class: "sd-grow" }, select, answer));
    });

    const ownAge = () => {
      if (info.suggested.age) return info.suggested.age;
      const born = new Date(`${draft.birthday}T00:00:00`);
      if (Number.isNaN(born.getTime())) return null;
      const now = new Date();
      return now.getFullYear() - born.getFullYear() - (now < new Date(now.getFullYear(), born.getMonth(), born.getDate()) ? 1 : 0);
    };
    const stepMeet = () => {
      const age = ownAge();
      const minAge = h("input", { type: "number", min: "18", max: "99", inputmode: "numeric", "aria-label": "Youngest age", value: draft.age_min, placeholder: age ? String(Math.max(18, age - 8)) : "18", oninput: (e) => { draft.age_min = e.currentTarget.value; } });
      const maxAge = h("input", { type: "number", min: "18", max: "99", inputmode: "numeric", "aria-label": "Oldest age", value: draft.age_max, placeholder: age ? String(Math.min(99, age + 8)) : "99", oninput: (e) => { draft.age_max = e.currentTarget.value; } });
      const picked = h("span", { class: "muted small", text: `${draft.interests.size}/${options.max_interests}` });
      return [
        h("div", { class: "field" }, h("span", { class: "sd-label", text: "I'm looking for" }), chips(LOOKING_FOR, draft.looking_for, { label: "I'm looking for" })),
        h("div", { class: "field" }, h("span", { class: "sd-label", text: "Show me" }), chips(SHOW_ME, draft.show_me, { label: "Show me" })),
        h("div", { class: "field" }, h("span", { class: "sd-label", text: "Ages" }), h("div", { class: "sd-range" }, minAge, h("span", { "aria-hidden": "true", text: "to" }), maxAge), h("span", { class: "hint", text: "Leave them empty for your own age, give or take eight years." })),
        h("div", { class: "field" }, h("span", { class: "sd-label" }, h("span", { text: "Your interests " }), picked), chips(INTERESTS, draft.interests, { multi: true, max: options.max_interests, label: "Your interests", onChange: () => { picked.textContent = `${draft.interests.size}/${options.max_interests}`; } })),
        h("div", { class: "sd-privacy" },
          toggle("Hide me from my Social friends", "Your friends don't see you here, and you don't see them.", draft.hide_friends, (on) => { draft.hide_friends = on; }),
          info.hosted ? toggle("Hide me from my own organization", "People of your organization don't see you here, and you don't see them.", draft.hide_home, (on) => { draft.hide_home = on; }) : h("p", { class: "muted small", text: "People of your own household never see each other here." })),
      ];
    };

    const check = () => {
      if (step === 0) {
        if (!draft.first_name.trim()) return "Enter your first name.";
        if (!info.suggested.birthday_known && !existing && !draft.birthday) return "Enter your birthday.";
        if (!draft.gender_sel.value) return "Choose how you describe yourself.";
        if (!existing && !draft.confirm_adult) return "Confirm that you are 18 or over.";
      }
      if (step === 2) {
        if (draft.prompts.some((p) => !p.id || !p.answer.trim())) return "Choose three prompts and answer each one.";
        if (new Set(draft.prompts.map((p) => p.id)).size < 3) return "Choose three different prompts.";
      }
      if (step === 3 && !draft.show_me.value) return "Choose who you would like to meet.";
      return "";
    };
    const payload = () => ({
      first_name: draft.first_name.trim(), gender: draft.gender_sel.value, city: draft.city.trim(), height_cm: draft.height_cm === "" ? null : draft.height_cm,
      bio: draft.bio.trim(), photos: draft.photos, prompts: draft.prompts.map((p) => ({ id: p.id, answer: p.answer.trim() })), interests: [...draft.interests],
      looking_for: draft.looking_for.value || "unsure", show_me: draft.show_me.value, age_min: draft.age_min === "" ? null : draft.age_min, age_max: draft.age_max === "" ? null : draft.age_max,
      hide_friends: draft.hide_friends, hide_home: draft.hide_home, ...(existing ? {} : { birthday: draft.birthday, confirm_adult: draft.confirm_adult }),
    });
    const save = async () => {
      try { await api("/me", { method: "PUT", body: payload() }); }
      catch (err) { fail(err.message); return; }
      Aira.toast(existing ? "Your dating profile is saved." : "You're in! Start meeting people.", "success");
      onDone();
    };
    const draw = () => {
      fail("");
      const content = [stepAbout, stepPhotos, stepPrompts, stepMeet][step]();
      const last = step === STEPS.length - 1;
      wrap.replaceChildren(
        h("div", { class: "sd-steps" },
          h("ol", { class: "sd-progress", "aria-label": "Steps" }, ...STEPS.map((item, i) => h("li", { class: i === step ? "on" : i < step ? "done" : "" },
            existing ? h("button", { type: "button", "aria-current": i === step ? "step" : null, "aria-label": item.title, onclick: () => { step = i; draw(); } }) : h("span", { "aria-current": i === step ? "step" : null })))),
          h("span", { class: "muted small", text: `Step ${step + 1} of ${STEPS.length}` })),
        h("h2", { id: "sdStepTitle", class: "sd-step-title", text: STEPS[step].title }),
        h("p", { class: "muted", text: STEPS[step].hint }),
        h("div", { class: "sd-step-body" }, ...content.filter(Boolean)),
        error,
        h("div", { class: "sd-step-actions" },
          step > 0 ? S.button("Back", () => { step -= 1; draw(); }, { kind: "ghost" }) : existing ? S.button("Cancel", () => onDone(), { kind: "ghost" }) : h("span"),
          existing && !last ? S.button("Save", async () => { const problem = check(); if (problem) { fail(problem); return; } await save(); }, { kind: "secondary" }) : null,
          S.button(last ? (existing ? "Save changes" : "Start meeting people") : "Next", async () => {
            const problem = check();
            if (problem) { fail(problem); return; }
            if (!last) { step += 1; draw(); wrap.scrollIntoView({ block: "start", behavior: "smooth" }); return; }
            await save();
          }, { kind: "primary sd-cta" })));
      const first = wrap.querySelector(".sd-step-body input, .sd-step-body select, .sd-step-body button");
      if (first && step > 0) first.focus({ preventScroll: true });
    };
    body.append(wrap);
    draw();
  }

  function welcome(body, info, go) {
    const points = [
      { icon: "🔞", title: "Adults only", text: "Dating is only for people 18 and over." },
      { icon: "🪪", title: "A separate profile", text: "Your first name, age, photos and prompts. Nothing appears on your Social profile or feed." },
      { icon: "💌", title: "Only matches can message", text: "When you both like each other, a private chat opens. Unmatch any time." },
    ];
    body.append(h("section", { class: "sd-hero" },
      h("div", { class: "sd-hero-art", "aria-hidden": "true" }, h("span", { class: "sd-bubble a", text: "☕" }), h("span", { class: "sd-bubble b" }, glyph("sdHeart")), h("span", { class: "sd-bubble c", text: "🌿" })),
      h("h2", { class: "sd-hero-title", text: "Meet someone new, at your own pace" }),
      h("p", { class: "sd-hero-sub", text: "Set up a dating profile in four short steps. It stays off until you turn it on." }),
      h("ul", { class: "sd-points" }, ...points.map((p) => h("li", {}, h("span", { class: "sd-point-icon", "aria-hidden": "true", text: p.icon }), h("div", {}, h("strong", { text: p.title }), h("span", { text: p.text }))))),
      h("div", { class: "sd-hero-actions" },
        S.button("Create my dating profile", () => go({ dv: "setup" }), { kind: "primary sd-cta" }),
        S.button("Safety tips", () => go({ dv: "safety" }), { kind: "ghost" }))));
  }

  // ---------------------------------------------------------------------------
  // Discover
  // ---------------------------------------------------------------------------
  function celebrate(match, me, go) {
    api(`/matches/${match.id}/seen`, { method: "POST", body: {}, silent: true }).catch(() => {});
    const them = match.profile || {};
    const dialog = Aira.modal({
      title: "You both said yes!",
      body: h("div", { class: "sd-celebrate" },
        h("div", { class: "sd-confetti", "aria-hidden": "true" }, ...Array.from({ length: 14 }, (_, i) => vars(h("span"), { "--i": i }))),
        h("div", { class: "sd-pair" }, face({ id: "me", first_name: me.first_name, photos: me.photos }, 96), h("span", { class: "sd-pair-heart", "aria-hidden": "true" }, glyph("sdHeart")), face(them, 96)),
        h("p", { class: "sd-celebrate-text", text: `You and ${them.first_name} like each other. Say hello while it's fresh.` })),
      actions: [
        { label: "Keep exploring", kind: "ghost" },
        { label: `Message ${them.first_name}`, kind: "primary", onClick: () => go({ dv: "matches", match: match.id }) },
      ],
    });
    dialog.box.classList.add("sd-celebrate-box");
  }

  async function discoverView(body, info, route, go, refreshCounts) {
    const mode = route.query.mode === "likes" ? "likes" : "";
    const data = await api(`/discover${mode ? "?mode=likes" : ""}`);
    if (data.paused) {
      body.append(h("section", { class: "card suite-section sd-empty" }, h("span", { class: "sd-empty-icon", "aria-hidden": "true", text: "⏸️" }), h("h2", { class: "suite-h2", text: "Your dating profile is paused" }), h("p", { class: "muted", text: "Nobody sees you in Discover and you see nobody. Your matches and chats are kept." }),
        S.button("Turn it back on", async () => { await api("/me/pause", { method: "POST", body: { paused: false } }); go({}); }, { kind: "primary sd-cta" })));
      return;
    }
    const queue = data.cards;
    let limits = data.limits;
    const stage = h("div", { class: "sd-stage" });
    const allowance = h("p", { class: "sd-allowance", "aria-live": "polite" });
    const drawAllowance = () => { allowance.textContent = `${count(limits.likes_left, "like", "likes")} and ${count(limits.stars_left, "star", "stars")} left today`; };
    const filters = async () => {
      const profile = info.profile;
      await S.edit({
        title: "Who you see",
        fields: [
          { name: "show_me", label: "Show me", type: "select", options: Object.entries(SHOW_ME) },
          { name: "age_min", label: "Youngest age", type: "number", min: 18, max: 99 },
          { name: "age_max", label: "Oldest age", type: "number", min: 18, max: 99 },
        ],
        values: { show_me: profile.show_me, age_min: profile.age_min, age_max: profile.age_max },
        submitLabel: "Show people",
        onSave: async (values) => { await api("/me", { method: "PUT", body: values }); go({ dv: "discover" }); },
      });
    };
    const head = h("div", { class: "sd-discover-head" },
      mode ? h("div", { class: "sd-mode" }, glyph("sdHeart"), h("span", { text: "People who like you" }), S.button("Everyone", () => go({ dv: "discover" }), { kind: "ghost sm" })) : h("span", { class: "sd-waiting" }),
      S.button("Filters", filters, { kind: "ghost sm", icon: "sdSliders" }));
    const waiting = head.querySelector(".sd-waiting");
    body.append(head, stage, allowance);

    let busy = false;
    const answer = async (action, cardNode) => {
      const current = queue[0];
      if (!current || busy) return;
      busy = true;
      try {
        const result = await api(`/profiles/${current.id}/swipe`, { method: "POST", body: { action } });
        limits = result.limits;
        if (cardNode) { cardNode.classList.add(action === "pass" ? "sd-out-left" : action === "star" ? "sd-out-up" : "sd-out-right"); await new Promise((r) => setTimeout(r, 260)); }
        queue.shift();
        remaining = Math.max(0, remaining - 1);
        if (result.match) { celebrate(result.match, info.profile, go); refreshCounts(); }
        draw();
      } catch (error) { Aira.reportError(error); }
      finally { busy = false; }
    };
    const undo = async () => {
      const result = await api("/undo", { method: "POST", body: {} });
      limits = result.limits;
      if (result.card) { queue.unshift(result.card); remaining += 1; }
      draw();
    };
    const roundButton = (kind, label, icon, onClick, disabled) => h("button", { type: "button", class: `sd-act sd-act-${kind}`, "aria-label": label, title: label, disabled: !!disabled, onclick: (event) => Aira.withBusy(event.currentTarget, onClick) }, glyph(icon));
    let remaining = data.remaining || queue.length;
    const draw = () => {
      drawAllowance();
      if (waiting) waiting.textContent = remaining ? (remaining === 1 ? "1 person to meet" : `${remaining} people to meet`) : "";
      const current = queue[0];
      if (!current) {
        stage.replaceChildren(h("section", { class: "card suite-section sd-empty" },
          h("span", { class: "sd-empty-icon", "aria-hidden": "true", text: mode ? "💌" : "🌅" }),
          h("h2", { class: "suite-h2", text: mode ? "You've seen everyone who likes you" : "You're all caught up" }),
          h("p", { class: "muted", text: mode ? "New likes show up under Likes you." : "New people join every day. Widen your ages or who you'd like to meet to see more now." }),
          h("div", { class: "btn-row", style: { justifyContent: "center" } },
            limits.can_undo ? S.button("Undo last pass", undo, { kind: "ghost", icon: "sdUndo" }) : null,
            mode ? S.button("See everyone", () => go({ dv: "discover" }), { kind: "secondary" }) : S.button("Change filters", filters, { kind: "secondary", icon: "sdSliders" }))));
        return;
      }
      const node = profileCard(current, { onReport: () => reportProfile(current, async () => { queue.shift(); draw(); }) });
      dragToAnswer(node, (action) => answer(action, node));
      const actions = h("div", { class: "sd-actions", role: "group", "aria-label": `Answer ${current.first_name}` },
        roundButton("undo", "Undo last pass", "sdUndo", undo, !limits.can_undo),
        roundButton("pass", `Pass on ${current.first_name}`, "sdClose", () => answer("pass", node)),
        roundButton("star", `Star ${current.first_name} (${limits.stars_left} left today)`, "sdStar", () => answer("star", node), !limits.stars_left || !limits.likes_left),
        roundButton("like", `Like ${current.first_name}`, "sdHeart", () => answer("like", node), !limits.likes_left));
      stage.replaceChildren(h("div", { class: "sd-deck" }, queue[1] ? h("div", { class: "sd-card sd-card-next", "aria-hidden": "true" }) : null, node), actions);
    };
    draw();
    // Arrow keys: left passes, right likes, up stars (not while typing or a dialog is open).
    const keys = (event) => {
      if (!stage.isConnected) { document.removeEventListener("keydown", keys); return; }
      if (event.target.closest && event.target.closest("input, textarea, select, .modal")) return;
      if (document.querySelector(".modal-backdrop")) return;
      const node = stage.querySelector(".sd-deck > .sd-card:not(.sd-card-next)");
      if (event.key === "ArrowLeft") { event.preventDefault(); answer("pass", node); }
      else if (event.key === "ArrowRight") { event.preventDefault(); if (limits.likes_left) answer("like", node); }
      else if (event.key === "ArrowUp" && limits.stars_left && limits.likes_left) { event.preventDefault(); answer("star", node); }
    };
    document.addEventListener("keydown", keys);
  }

  // Drag the photo sideways to answer (a phone's natural gesture); a short tap still flips photos.
  function dragToAnswer(node, onAnswer) {
    const photo = node.querySelector(".sd-photo");
    let start = null;
    photo.addEventListener("pointerdown", (event) => { if (event.button !== 0 || event.target.closest("button")) return; start = { x: event.clientX, y: event.clientY, id: event.pointerId }; });
    photo.addEventListener("pointermove", (event) => {
      if (!start || event.pointerId !== start.id) return;
      const dx = event.clientX - start.x;
      if (Math.abs(dx) < 8) return;
      photo.setPointerCapture(event.pointerId);
      node.style.transform = `translateX(${dx}px) rotate(${dx / 24}deg)`;
      node.dataset.lean = dx > 40 ? "like" : dx < -40 ? "pass" : "";
    });
    const end = (event) => {
      if (!start || event.pointerId !== start.id) return;
      const dx = event.clientX - start.x;
      start = null;
      node.style.transform = "";
      node.dataset.lean = "";
      if (dx > 110) onAnswer("like");
      else if (dx < -110) onAnswer("pass");
    };
    photo.addEventListener("pointerup", end);
    photo.addEventListener("pointercancel", end);
  }

  // ---------------------------------------------------------------------------
  // Likes you
  // ---------------------------------------------------------------------------
  async function likesView(body, go) {
    const data = await api("/likes");
    if (!data.count) {
      body.append(h("section", { class: "card suite-section sd-empty" }, h("span", { class: "sd-empty-icon", "aria-hidden": "true", text: "💌" }), h("h2", { class: "suite-h2", text: "No likes yet" }), h("p", { class: "muted", text: "When someone likes you, they show up here. Three thoughtful prompts and two clear photos help." }), S.button("Discover people", () => go({ dv: "discover" }), { kind: "secondary" })));
      return;
    }
    body.append(h("section", { class: "card suite-section sd-likes" },
      h("div", { class: "sd-likes-head" }, h("strong", { class: "sd-big", text: String(data.count) }), h("div", {}, h("h2", { class: "suite-h2", text: data.count === 1 ? "person likes you" : "people like you" }), h("p", { class: "muted", text: "Meet them one by one. Like them back to match; a pass is never told." }))),
      h("ul", { class: "sd-hints" }, ...data.hints.map((hint) => vars(h("li", { class: "sd-hint" },
        h("span", { class: "sd-hint-blur", "aria-hidden": "true" }, h("span", { text: hint.initial })),
        hint.star ? h("span", { class: "sd-hint-star" }, glyph("sdStar"), h("span", { class: "sr-only", text: "Starred you" })) : null,
        h("span", { class: "sd-hint-text", text: hint.shared.length ? `${hint.shared.length} in common: ${hint.shared.slice(0, 2).map(plainInterest).join(", ")}` : "New to you" })), { "--sd-tint": hint.tint }))),
      h("div", { class: "btn-row", style: { justifyContent: "center" } }, S.button("Meet them", () => go({ dv: "discover", mode: "likes" }), { kind: "primary sd-cta", icon: "sdHeart" }))));
  }

  // ---------------------------------------------------------------------------
  // Matches and a match's chat
  // ---------------------------------------------------------------------------
  async function matchesView(body, go) {
    const data = await api("/matches");
    if (!data.matches.length) {
      body.append(h("section", { class: "card suite-section sd-empty" }, h("span", { class: "sd-empty-icon", "aria-hidden": "true", text: "✨" }), h("h2", { class: "suite-h2", text: "No matches yet" }), h("p", { class: "muted", text: "When you and someone like each other, you can chat here." }), S.button("Discover people", () => go({ dv: "discover" }), { kind: "secondary" })));
      return;
    }
    const fresh = data.matches.filter((m) => !m.last);
    const talking = data.matches.filter((m) => m.last);
    if (fresh.length) body.append(h("section", { class: "card suite-section" }, h("h2", { class: "suite-h2", text: "New matches" }),
      h("ul", { class: "sd-new-matches" }, ...fresh.map((m) => h("li", {}, h("a", { href: `#/social?${new URLSearchParams({ ...currentQuery(), dv: "matches", match: m.id })}`, class: `sd-new-match ${m.new ? "sd-unseen" : ""}`.trim() }, face(m.profile, 72), h("span", { text: m.profile.first_name }), m.new ? h("span", { class: "sd-new-dot", text: "New" }) : null))))));
    if (talking.length) body.append(h("section", { class: "card suite-section" }, h("h2", { class: "suite-h2", text: "Conversations" }),
      h("ul", { class: "sd-convos" }, ...talking.map((m) => h("li", {}, h("a", { href: `#/social?${new URLSearchParams({ ...currentQuery(), dv: "matches", match: m.id })}`, class: "sd-convo" },
        face(m.profile, 52),
        h("span", { class: "sd-convo-text" }, h("strong", { text: m.profile.first_name }), h("span", { class: `sd-convo-last ${m.unread ? "sd-bold" : ""}`.trim(), text: `${m.last.mine ? "You: " : ""}${m.last.text || "Message deleted"}` })),
        h("span", { class: "sd-convo-meta" }, h("span", { class: "muted small", text: when(m.last.at) }), m.unread ? h("span", { class: "sd-unread", "aria-label": `${m.unread} unread`, text: String(m.unread) }) : null)))))));
  }
  let lastRoute = { tab: "dating" };
  const currentQuery = () => ({ tab: lastRoute.tab || "dating" });

  async function chatView(body, matchId, go, refreshCounts) {
    const { match } = await api(`/matches/${matchId}`);
    if (match.new) api(`/matches/${matchId}/seen`, { method: "POST", body: {}, silent: true }).then(refreshCounts).catch(() => {});
    const them = match.profile;
    const first = await api(`/matches/${matchId}/messages`);
    const state = { messages: first.messages, now: first.now, readUpto: first.read_upto || "", canSend: first.can_send };
    const thread = h("div", { class: "sd-thread", role: "log", "aria-live": "polite", "aria-label": `Messages with ${them.first_name}` });
    const input = h("textarea", { rows: "1", maxlength: "4000", "aria-label": "Your message", placeholder: state.canSend ? `Message ${them.first_name}` : "This chat is closed.", disabled: !state.canSend });
    const ideas = h("div", { class: "sd-ideas" });
    const drawIdeas = () => {
      ideas.hidden = state.messages.some((m) => !m.removed) || !state.canSend;
      ideas.replaceChildren(h("p", { class: "muted small", text: "Not sure how to start? Tap an idea, then make it yours." }),
        h("div", { class: "sd-idea-list" }, ...match.icebreakers.map((idea) => h("button", { type: "button", class: "sd-idea", text: idea, onclick: () => { input.value = idea; input.focus(); input.setSelectionRange(idea.length, idea.length); } }))));
    };
    const draw = (follow = false) => {
      const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80;
      thread.replaceChildren(...(state.messages.length ? [] : [h("div", { class: "sd-thread-start" }, face(them, 84), h("p", { text: `You matched with ${them.first_name} ${when(match.created_at) === "Just now" ? "just now" : when(match.created_at)}.` }))]),
        ...state.messages.map((item) => {
          const seen = item.mine && state.readUpto && item.created_at <= state.readUpto;
          return h("div", { class: `sd-msg ${item.mine ? "mine" : ""}`.trim() },
            item.mine ? null : face(them, 30),
            h("div", { class: "sd-msg-col" },
              h("div", { class: "sd-bubble-msg" }, item.deleted ? h("em", { text: "This message was deleted." }) : null, item.text ? h("span", { text: item.text }) : null,
                item.photo && safePhoto(item.photo) ? h("img", { src: item.photo, alt: item.mine ? "Your photo" : `Photo from ${them.first_name}`, loading: "lazy", referrerpolicy: "no-referrer" }) : null),
              h("span", { class: "muted small", text: `${when(item.created_at)}${item.mine && !item.deleted ? (seen ? " · Seen" : " · Sent") : ""}` })));
        }));
      drawIdeas();
      if (follow || atBottom) thread.scrollTop = thread.scrollHeight;
    };
    const markRead = () => api(`/matches/${matchId}/read`, { method: "POST", body: {}, silent: true }).then(refreshCounts).catch(() => {});
    const poll = async (follow = false) => {
      const data = await api(`/matches/${matchId}/messages?since=${encodeURIComponent(state.now)}`, { silent: true });
      state.now = data.now;
      let changed = follow;
      for (const item of data.messages) {
        const index = state.messages.findIndex((m) => m.id === item.id);
        if (item.removed) { if (index >= 0) state.messages.splice(index, 1); changed = true; continue; }
        if (index >= 0) state.messages[index] = item; else state.messages.push(item);
        changed = true;
      }
      if (data.messages.some((m) => !m.mine)) markRead();
      if ((data.read_upto || "") !== state.readUpto) { state.readUpto = data.read_upto || ""; changed = true; }
      if (data.can_send !== state.canSend) { state.canSend = data.can_send; input.disabled = !state.canSend; changed = true; }
      if (changed) draw(follow);
    };
    const send = async () => {
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      await api(`/matches/${matchId}/messages`, { method: "POST", body: { text } });
      input.value = "";
      input.style.height = "";
      await poll(true);
      input.focus();
    };
    input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); Aira.withBusy(sendButton, send); } });
    input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = `${Math.min(140, input.scrollHeight)}px`; });
    const sendButton = h("button", { type: "button", class: "btn primary sd-send", disabled: !state.canSend, onclick: (event) => Aira.withBusy(event.currentTarget, send) }, h("span", { text: "Send" }));
    const unmatch = async () => {
      if (!(await Aira.confirm({ title: `Unmatch ${them.first_name}?`, message: "Your chat is deleted for both of you and you won't see each other again. They are not told why.", confirmLabel: "Unmatch", danger: true }))) return;
      await api(`/matches/${matchId}`, { method: "DELETE" });
      Aira.toast(`You unmatched ${them.first_name}.`, "success");
      go({ dv: "matches" });
    };
    const blockThem = async () => {
      if (!(await Aira.confirm({ title: `Block ${them.first_name}?`, message: "The match and chat end, and they cannot find or message you anywhere in Social.", confirmLabel: "Block", danger: true }))) return;
      await api(`/profiles/${them.id}/block`, { method: "POST", body: {} });
      Aira.toast(`${them.first_name} is blocked.`, "success");
      go({ dv: "matches" });
    };
    const seeProfile = () => Aira.modal({ title: them.first_name, wide: true, body: h("div", { class: "sd-root sd-modal-card" }, profileCard(them)), actions: [{ label: "Close", kind: "ghost" }] });
    const head = h("div", { class: "sd-chat-head" },
      S.button("Matches", () => go({ dv: "matches" }), { kind: "ghost sm" }),
      h("button", { type: "button", class: "sd-chat-who", onclick: seeProfile, "aria-label": `See ${them.first_name}'s profile` }, face(them, 44), h("span", {}, h("strong", { text: them.first_name }), h("span", { class: "muted small", text: [them.age, them.city].filter(Boolean).join(" · ") }))),
      h("div", { class: "sd-chat-tools" },
        S.button("Unmatch", unmatch, { kind: "ghost sm" }),
        S.button("Block", blockThem, { kind: "ghost sm" }),
        S.button("Report", () => reportProfile(them, async () => go({ dv: "matches" })), { kind: "ghost sm" })));
    body.append(h("section", { class: "card suite-section sd-chat" }, head, thread, ideas,
      h("div", { class: "sd-composer" }, input, sendButton)));
    draw(true);
    markRead();
    S.live(poll, 2000, thread);
  }

  // ---------------------------------------------------------------------------
  // My dating profile
  // ---------------------------------------------------------------------------
  function profileView(body, info, go) {
    const me = info.profile;
    const paused = me.status === "paused";
    const setPaused = async (on) => { await api("/me/pause", { method: "POST", body: { paused: on } }); Aira.toast(on ? "Your dating profile is paused. Your matches are kept." : "Welcome back! People can see you again.", "success"); go({ dv: "profile" }); };
    const remove = async () => {
      if (!(await Aira.confirm({ title: "Delete your dating profile?", message: "Your dating profile, likes, matches and their chats are deleted for good. Your Social profile stays as it is.", confirmLabel: "Delete", danger: true }))) return;
      await api("/me", { method: "DELETE" });
      Aira.toast("Your dating profile is deleted.", "success");
      go({ dv: "discover" });
    };
    const privacy = async (field, on, input) => {
      try { await api("/me", { method: "PUT", body: { [field]: on } }); Aira.toast("Saved.", "success"); }
      catch (error) { input.checked = !on; Aira.reportError(error); }
    };
    body.append(
      h("section", { class: `sd-status ${paused ? "paused" : ""}`.trim() },
        h("span", { class: "sd-status-dot", "aria-hidden": "true" }),
        h("div", { class: "sd-grow" }, h("strong", { text: paused ? "Paused" : "Active" }), h("span", { class: "muted small", text: paused ? "Nobody sees you in Discover. Your matches and chats are kept." : "People who fit your preferences can see you." })),
        S.button(paused ? "Turn back on" : "Pause", () => setPaused(!paused), { kind: paused ? "primary" : "secondary" })),
      h("div", { class: "sd-profile-grid" },
        h("div", { class: "sd-preview" }, h("p", { class: "muted small sd-preview-label", text: "How others see you" }), profileCard(me, { preview: true })),
        h("div", { class: "sd-side" },
          h("section", { class: "card suite-section" }, h("h2", { class: "suite-h2", text: "Your profile" }),
            h("dl", { class: "sd-summary" },
              h("dt", { text: "Show me" }), h("dd", { text: SHOW_ME[me.show_me] }),
              h("dt", { text: "Ages" }), h("dd", { text: `${me.age_min} to ${me.age_max}` }),
              h("dt", { text: "Looking for" }), h("dd", { text: LOOKING_FOR[me.looking_for] })),
            h("div", { class: "btn-row" }, S.button("Edit profile", () => go({ dv: "edit" }), { kind: "primary" }), S.button("Edit prompts", () => go({ dv: "edit", step: "2" }), { kind: "secondary" }))),
          h("section", { class: "card suite-section" }, h("h2", { class: "suite-h2", text: "Privacy" }),
            toggle("Hide me from my Social friends", "Your friends don't see you here, and you don't see them.", me.hide_friends, (on, input) => privacy("hide_friends", on, input)),
            info.hosted ? toggle("Hide me from my own organization", "People of your organization don't see you here, and you don't see them.", me.hide_home, (on, input) => privacy("hide_home", on, input)) : h("p", { class: "muted small", text: "People of your own household never see each other here." }),
            h("p", { class: "muted small", text: "Blocked people never see you, and you never see them." })),
          h("section", { class: "card suite-section" }, h("h2", { class: "suite-h2", text: "Your data" }),
            h("div", { class: "btn-row" },
              S.button("Download my dating data", () => Aira.download("/api/social/dating/me/export", "dating.json"), { kind: "ghost" }),
              S.button("Safety tips", () => go({ dv: "safety" }), { kind: "ghost", icon: "sdShield" }),
              S.button("Delete dating profile", remove, { kind: "danger" }))))));
  }

  // ---------------------------------------------------------------------------
  // The section
  // ---------------------------------------------------------------------------
  const VIEWS = [
    { id: "discover", label: "Discover", icon: "sdSpark" },
    { id: "likes", label: "Likes you", icon: "sdHeart" },
    { id: "matches", label: "Matches", icon: "sdChat" },
    { id: "profile", label: "Profile", icon: "sdUser" },
    { id: "safety", label: "Safety", icon: "sdShield" },
  ];

  // `render` (optional) is the section's own redraw; Dating moves between its views through the address instead.
  // eslint-disable-next-line no-unused-vars
  async function tab(holder, route, render) {
    const query = (route && route.query) || {};
    lastRoute = { tab: query.tab || "dating" };
    const go = (next) => Aira.router.go(["social"], { tab: query.tab || "dating", ...next });
    const root = h("div", { class: "sd-root" });
    holder.replaceChildren(root);
    root.append(Aira.ui.skeleton(3));
    let info;
    try { info = await api("/me"); }
    catch (error) {
      if (error.status === 403) {
        root.replaceChildren(h("section", { class: "card suite-section sd-empty" }, h("span", { class: "sd-empty-icon", "aria-hidden": "true", text: "🔞" }), h("h2", { class: "suite-h2", text: "Dating is for adults" }), h("p", { class: "muted", text: "Dating is only for people 18 and over." })));
        return;
      }
      throw error;
    }
    root.replaceChildren();
    const view = query.dv || "discover";
    if (!info.profile) {
      if (view === "safety") { root.append(h("div", { class: "btn-row" }, S.button("Back", () => go({}), { kind: "ghost sm" }))); safetyView(root); return; }
      if (view === "setup") { wizard(root, info, () => go({ dv: "discover" })); return; }
      welcome(root, info, go);
      return;
    }
    if (view === "edit") { wizard(root, info, () => go({ dv: "profile" }), Math.max(0, Math.min(3, Number(query.step) || 0))); return; }
    const nav = h("nav", { class: "sd-subnav", "aria-label": "Dating" });
    const active = view === "setup" ? "discover" : view;
    const drawNav = (badge) => nav.replaceChildren(...VIEWS.map((item) => {
      const n = item.id === "likes" ? badge.likes : item.id === "matches" ? badge.matches + badge.messages : 0;
      return h("button", { type: "button", class: `sd-subnav-item ${item.id === active ? "on" : ""}`.trim(), "aria-current": item.id === active ? "page" : null, "aria-label": n ? `${item.label} (${n})` : item.label, onclick: () => go({ dv: item.id }) },
        glyph(item.icon), h("span", { class: "sd-subnav-label", text: item.label }), n ? h("span", { class: "sd-count", "aria-hidden": "true", text: n > 99 ? "99+" : String(n) }) : null);
    }));
    drawNav(info.badge);
    const refreshCounts = async () => { try { const badge = await api("/badge", { silent: true }); drawNav(badge); if (Aira.socialDating.onBadge) Aira.socialDating.onBadge(badge); } catch { /* the next view tries again */ } };
    const body = h("div", { class: "sd-view" });
    root.append(nav, body);
    if (view === "likes") await likesView(body, go);
    else if (view === "matches" && query.match) await chatView(body, query.match, go, refreshCounts);
    else if (view === "matches") await matchesView(body, go);
    else if (view === "profile") profileView(body, info, go);
    else if (view === "safety") safetyView(body);
    else await discoverView(body, info, route, go, refreshCounts);
  }

  Aira.socialDating = {
    label: "Dating",
    icon: "sdHeart",
    about: "Meet new people: adults only, with a separate dating profile.",
    // Children and teens never see Dating (the server refuses them too).
    available: (ageGroup) => adultGroup(ageGroup || "adult"),
    tab,
    // New matches and unread dating messages, for the section's count (Social's own badges leave them out).
    badge: async () => {
      try { const b = await api("/badge", { silent: true }); return (b.matches || 0) + (b.messages || 0); }
      catch { return 0; }
    },
  };
})();

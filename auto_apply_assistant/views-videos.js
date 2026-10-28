/* Aira — Videos (#/videos): watch, upload and earn.
 *
 * Tabs (?tab=): home (subscriptions, popular, new, categories, search with ?q=), reels (one tall video at a time:
 * scroll, swipe, arrow keys or the buttons), films (short films by genre), library (history, watch later, playlists,
 * subscriptions), studio (your channel, uploads, your videos, copyright notices, notifications), earnings (adults: the
 * ads on your videos, payouts) and, for the household's owner, moderation. ?watch=<id> is the watch page (player,
 * the ads before and beside it, likes, comments, subscribe, share), ?channel=<id> a channel, ?playlist=<id> a playlist.
 *
 * Everything runs through /api/videos/* (aira/suite/videos/videos_routes.py) as the person using Aira (life.js).
 * Files go up in resumable parts (Aira.videos.uploadFile); the picture can be a frame picked from the video in the
 * browser. Media addresses are signed by the server. DOM is built with h() and textContent only.
 *
 * In the Aira frame (shell.js): "+ New" uploads a video, a reel or a short film, makes a playlist or (adults) adds a
 * brand deal, each through ?new=<kind>; the search box across the top suggests videos, channels, your own videos and
 * your playlists, and Enter lists the results on Home (?q=); the tabs are the left menu's folders.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("videos", path, opts);
  const go = (query) => Aira.router.go(["videos"], query);
  // A chip, part or filter button rebuilds the whole view; keep the keyboard on the button pressed (its group's
  // aria-label and its words find it again once the view is drawn).
  let refocus = null;
  const goFrom = (event, query) => {
    const button = event && event.currentTarget;
    const group = button && button.parentElement ? button.parentElement.closest("[aria-label]") : null;
    refocus = group ? [group.getAttribute("aria-label"), button.textContent] : null;
    go(query);
  };
  const restoreFocus = (main) => {
    if (!refocus) return;
    const [group, text] = refocus;
    refocus = null;
    const target = [...main.querySelectorAll("[aria-label] > button")].find((button) => button.parentElement.getAttribute("aria-label") === group && button.textContent === text);
    if (target) target.focus({ preventScroll: false });
  };

  const CATEGORY = { film: "Film and animation", music: "Music", comedy: "Comedy", education: "Learning", gaming: "Gaming", sports: "Sports", news: "News", howto: "How-to", travel: "Travel", food: "Food", family: "Family", animals: "Animals", science: "Science and tech", other: "Other" };
  const GENRE = { drama: "Drama", comedy: "Comedy", documentary: "Documentary", animation: "Animation", horror: "Horror", thriller: "Thriller", romance: "Romance", scifi: "Science fiction", experimental: "Experimental", other: "Other" };
  const RATING = { everyone: "For everyone", teen: "Teens and older", mature: "Adults only" };
  const VISIBILITY = { public: "Everyone (listed)", unlisted: "Anyone with the link", private: "Only me" };
  const KIND = { video: "Video", reel: "Reel", short: "Short film" };
  const BLOCKED = { hidden: "Hidden by a moderator", taken_down: "Taken down after a copyright notice", suspended: "Your channel is suspended" };
  const TIP_STATUS = { pledged: "Waiting for the money", received: "Received, thank you", declined: "Marked not received", cancelled: "Withdrawn" };
  const NOTICE = { received: "Received", taken_down: "Taken down", rejected: "Rejected", counter_filed: "Counter-notice sent", restored: "Back up", upheld: "Kept down" };
  const LANGUAGES = [["en", "English"], ["te", "Telugu"], ["hi", "Hindi"], ["ta", "Tamil"], ["kn", "Kannada"], ["ml", "Malayalam"], ["mr", "Marathi"], ["bn", "Bengali"], ["gu", "Gujarati"], ["pa", "Punjabi"], ["ur", "Urdu"], ["es", "Spanish"], ["fr", "French"], ["de", "German"], ["ar", "Arabic"], ["zh", "Chinese"], ["ja", "Japanese"], ["pt", "Portuguese"]];
  const options = (labels) => Object.entries(labels);

  // The app's own icons (the left menu's folders, "+ New", the search suggestions), drawn like Aira.ICONS.
  const GLYPHS = {
    vidUpload: '<svg viewBox="0 0 24 24"><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5"/><path d="M4 14v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/></svg>',
    vidReel: '<svg viewBox="0 0 24 24"><rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="m11 9.5 3.5 2.5-3.5 2.5z"/></svg>',
    vidFilm: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 12h4M3 16h4M17 8h4M17 12h4M17 16h4"/></svg>',
    vidPlaylist: '<svg viewBox="0 0 24 24"><path d="M4 6h12M4 11h12M4 16h7"/><path d="m15 14 5 3-5 3z"/></svg>',
    vidLibrary: '<svg viewBox="0 0 24 24"><rect x="3" y="7" width="14" height="13" rx="2"/><path d="M7 4h12a2 2 0 0 1 2 2v10"/><path d="m9 11 4 2.5-4 2.5z"/></svg>',
    vidDeal: '<svg viewBox="0 0 24 24"><path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.5"/></svg>',
    vidEarn: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.5-1-1.6-1.5-3-1.5-1.7 0-3 .8-3 2s1.3 1.7 3 2 3 .8 3 2-1.3 2-3 2c-1.4 0-2.5-.5-3-1.5M12 6v2M12 16v2"/></svg>',
    vidFlag: '<svg viewBox="0 0 24 24"><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;
  const KIND_ICON = { video: "videos", reel: "vidReel", short: "vidFilm" };

  // What the frame needs between page draws: the person's channel (/me, learned when the page is drawn), what
  // "+ New" asked for (?new=, answered once the page is drawn), what the drawn page can answer itself, and the
  // answers behind the search suggestions (a few seconds old at most).
  let known = null;
  let pending = "";
  const hooks = {};
  const answers = new Map();
  const cached = (path, ms = 8000) => {
    const hit = answers.get(path);
    if (hit && Date.now() - hit.at < ms) return hit.promise;
    const promise = api(path, { silent: true }).catch(() => null);
    answers.set(path, { at: Date.now(), promise });
    if (answers.size > 40) answers.delete(answers.keys().next().value);
    return promise;
  };

  // ---------------------------------------------------------------- small helpers
  const usd = (micros) => {
    const value = Number(micros || 0) / 1e6;
    if (value > 0 && value < 0.01) return `$${value.toFixed(4)}`;
    return S.money(Math.round(value * 100), "USD");
  };
  const clockText = (seconds) => {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const hours = Math.floor(total / 3600), minutes = Math.floor((total % 3600) / 60), secs = total % 60;
    return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}` : `${minutes}:${String(secs).padStart(2, "0")}`;
  };
  const viewsText = (n) => (Number(n) === 1 ? "1 view" : `${Number(n || 0).toLocaleString()} views`);
  const likesText = (n) => (Number(n) === 1 ? "1 like" : `${Number(n || 0).toLocaleString()} likes`);
  const subscribersText = (n) => (Number(n) === 1 ? "1 subscriber" : `${Number(n || 0).toLocaleString()} subscribers`);
  const when = (iso) => {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const minutes = Math.round((Date.now() - date.getTime()) / 60000);
    if (minutes < 1) return "Just now";
    if (minutes < 60) return minutes === 1 ? "1 minute ago" : `${minutes} minutes ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
    const days = Math.round(hours / 24);
    if (days < 30) return days === 1 ? "Yesterday" : `${days} days ago`;
    return date.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
  };
  const lower = (text) => (text ? text.charAt(0).toLowerCase() + text.slice(1) : "");
  // File sizes the way people say them ("2 GB", not "2048.0 MB").
  const size = (bytes) => {
    const value = Number(bytes || 0);
    if (value >= 1024 ** 3) return `${Number((value / 1024 ** 3).toFixed(1))} GB`;
    if (value >= 1024 ** 2) return `${Number((value / 1024 ** 2).toFixed(1))} MB`;
    return `${Math.max(1, Math.round(value / 1024))} KB`;
  };
  const percent = (part, whole) => (whole ? Math.max(0, Math.min(100, Math.round((100 * part) / whole))) : 0);
  // A button whose words change after it is pressed (Like, Subscribe, Watch later, Sound). Aira.withBusy puts the
  // old words back when it finishes, so these handle their own busy state.
  function toggle(label, onClick, { kind = "ghost", pressed } = {}) {
    const text = h("span", { text: label });
    const button = h("button", { type: "button", class: `btn ${kind}`.trim() }, text);
    if (pressed !== undefined) button.setAttribute("aria-pressed", String(!!pressed));
    const set = (next, on) => { text.textContent = next; if (on !== undefined) button.setAttribute("aria-pressed", String(!!on)); };
    button.addEventListener("click", async () => {
      if (button.getAttribute("aria-busy") === "true") return;
      button.setAttribute("aria-busy", "true");
      button.disabled = true;
      try { await onClick(set); } catch (error) { Aira.reportError(error); } finally { button.removeAttribute("aria-busy"); button.disabled = false; }
    });
    return button;
  }
  // How far into a video the person got (Continue watching, History).
  const progressBar = (video) => (video.position && video.duration ? h("span", { class: "vid-watched", role: "img", "aria-label": `Watched ${percent(video.position, video.duration)}%` }, h("span", { style: { width: `${percent(video.position, video.duration)}%` } })) : null);
  const face = (channel) => h("span", { class: "vid-face", "aria-hidden": "true", text: (channel && channel.avatar) || "🙂" });
  const channelLink = (channel) => h("a", { href: `#/videos?channel=${channel.id}`, text: channel.name, style: { fontWeight: "600" } });
  const adult = (channel) => channel && (channel.age_group === "adult" || channel.age_group === "senior");

  // ---------------------------------------------------------------- uploads (resumable, in parts)
  const toBase64 = (blob) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(reader.error || new Error("The file could not be read."));
    reader.readAsDataURL(blob);
  });

  // Upload a file in parts. An upload that was interrupted (a closed tab, a lost connection) resumes from where the
  // server says it got to, as long as the same file is chosen again on this device.
  async function uploadFile(file, kind, { base = "/api/videos", onProgress } = {}) {
    const key = `upload:${base}:${kind}:${file.name || "file"}:${file.size}:${file.lastModified || 0}`;
    let upload = null;
    const saved = Aira.recall(key, "");
    if (saved) {
      try { upload = (await Aira.api(`${base}/uploads/${saved}`, { silent: true })).upload; } catch { upload = null; }
    }
    if (!upload) {
      upload = (await Aira.api(`${base}/uploads`, { method: "POST", body: { kind, filename: file.name || `${kind}`, size: file.size }, silent: true })).upload;
      Aira.remember(key, upload.id);
    }
    let failures = 0;
    while (upload.received < upload.size) {
      const part = file.slice(upload.received, Math.min(upload.size, upload.received + upload.chunk_bytes));
      try {
        upload = (await Aira.api(`${base}/uploads/${upload.id}/chunk`, { method: "POST", body: { offset: upload.received, data: await toBase64(part) }, silent: true })).upload;
        failures = 0;
      } catch (error) {
        failures += 1;
        // Retry a part that may have been cut short (a 400 with no reason, a server or network error), never an
        // explained refusal (a 400 with a code: wrong file type, too large).
        const explained = error && error.status === 400 && error.code;
        if (failures > 3 || explained || (error && error.status && error.status !== 400 && error.status < 500)) { if (error.status === 404) Aira.remember(key, ""); throw error; }
        await new Promise((resolve) => setTimeout(resolve, 1000 * failures));
        upload = (await Aira.api(`${base}/uploads/${upload.id}`, { silent: true })).upload; // where the server got to
      }
      if (onProgress) onProgress(upload.received / upload.size);
    }
    const done = (await Aira.api(`${base}/uploads/${upload.id}/complete`, { method: "POST", body: {}, silent: true })).file;
    Aira.remember(key, "");
    return done;
  }

  // The length and size of a video the person picked (a browser-recorded WebM reports its length only after a seek).
  function readMetadata(videoEl) {
    return new Promise((resolve) => {
      const finish = () => resolve({ duration: Number.isFinite(videoEl.duration) ? videoEl.duration : 0, width: videoEl.videoWidth || 0, height: videoEl.videoHeight || 0 });
      const onReady = () => {
        if (Number.isFinite(videoEl.duration)) { finish(); return; }
        videoEl.addEventListener("durationchange", function settle() { if (Number.isFinite(videoEl.duration)) { videoEl.removeEventListener("durationchange", settle); videoEl.currentTime = 0; finish(); } });
        videoEl.currentTime = 1e101;
      };
      if (videoEl.readyState >= 1) onReady(); else videoEl.addEventListener("loadedmetadata", onReady, { once: true });
      videoEl.addEventListener("error", () => resolve({ duration: 0, width: 0, height: 0 }), { once: true });
      setTimeout(finish, 8000);
    });
  }

  // A picture of one moment of the video, drawn in the browser.
  function captureFrame(videoEl, at) {
    return new Promise((resolve) => {
      const draw = () => {
        const width = Math.min(1280, videoEl.videoWidth || 640);
        const height = Math.round(width * ((videoEl.videoHeight || 360) / (videoEl.videoWidth || 640)));
        const canvas = h("canvas", { width: String(width), height: String(height) });
        try { canvas.getContext("2d").drawImage(videoEl, 0, 0, width, height); } catch { resolve(null); return; }
        canvas.toBlob((blob) => resolve(blob), "image/jpeg", 0.85);
      };
      if (at === undefined || Math.abs(videoEl.currentTime - at) < 0.05) { draw(); return; }
      videoEl.addEventListener("seeked", draw, { once: true });
      videoEl.currentTime = at;
      setTimeout(() => resolve(null), 6000);
    });
  }
  Aira.videos = { uploadFile, readMetadata, captureFrame, usd };

  // ---------------------------------------------------------------- cards and lists
  function thumb(video) {
    const box = h("div", { class: `vid-thumb${video.kind === "reel" ? " tall" : ""}` });
    if (video.thumb_url) box.append(h("img", { src: video.thumb_url, alt: "", loading: "lazy" }));
    else box.append(h("span", { class: "vid-play", "aria-hidden": "true", text: "▶" }));
    if (video.kind !== "video" || video.members_only) box.append(h("span", { class: "vid-kind", text: [video.kind !== "video" ? KIND[video.kind] : "", video.members_only ? "For members" : ""].filter(Boolean).join(" · ") }));
    if (video.duration) box.append(h("span", { class: "vid-badge", text: clockText(video.duration) }));
    const bar = progressBar(video);
    if (bar) box.append(bar);
    return box;
  }
  function card(video, { sub, open } = {}) {
    return h("button", { type: "button", class: "vid-card", "aria-label": `${video.title}, ${video.channel.name}`, onclick: () => (open ? open(video) : go({ watch: video.id })) },
      thumb(video),
      h("span", { class: "vid-card-title", text: video.title }),
      h("span", { class: "vid-meta", text: sub || `${video.channel.name} · ${viewsText(video.views)} · ${when(video.created_at)}` }));
  }
  const grid = (videos, empty, sub) => (videos && videos.length ? h("div", { class: "vid-grid" }, ...videos.map((video) => card(video, { sub: sub ? sub(video) : undefined }))) : Aira.ui.empty(empty[0], empty[1], empty[2]));
  // The next step under an empty list: upload one (the same form as "+ New"), or a link elsewhere in the app.
  const canUpload = (info) => !(info && info.channel && info.channel.suspended_at);
  const uploadLink = (info, label, kind = "video") => (canUpload(info) ? h("a", { class: "btn primary", href: `#/videos?tab=studio&new=${kind}`, text: label }) : null);
  const homeLink = (label) => h("a", { class: "btn secondary", href: "#/videos?tab=home", text: label });

  const report = (info, targetType, targetId) => S.edit({
    title: targetType === "video" ? "Report this video" : targetType === "comment" ? "Report this comment" : "Report this channel",
    fields: [{ name: "reason", label: "What is wrong?", type: "select", options: info.report_reasons.map((reason) => [reason, reason]) }, { name: "detail", label: "Tell us more (optional)", type: "textarea", rows: 3 }],
    submitLabel: "Send report",
    onSave: async (values) => { await api("/reports", { method: "POST", body: { target_type: targetType, target_id: targetId, reason: [values.reason, values.detail].filter(Boolean).join(": ") } }); Aira.toast("Thank you. Your report was sent to the moderators.", "success"); },
  });

  // ---------------------------------------------------------------- ads on the player
  const serveAd = async (videoId, slot) => { try { return (await api("/ads/serve", { method: "POST", body: { video_id: videoId, slot }, silent: true })).ad; } catch { return null; } };
  const seenAd = (ad) => api(`/ads/${ad.impression_id}/seen`, { method: "POST", body: {}, silent: true }).catch(() => null);
  const adLink = (ad, kind = "secondary sm") => h("a", { class: `btn ${kind}`, href: ad.click_url, target: "_blank", rel: "noopener noreferrer", text: ad.cta || "Learn more" });

  // A skippable video ad before the video: skip after five seconds; it counts after two seconds of playing.
  function playPreroll(box, ad) {
    return new Promise((resolve) => {
      let finished = false, reported = false;
      const adVideo = h("video", { src: ad.media_url, autoplay: true, playsinline: true, "aria-label": `Advertisement from ${ad.advertiser}` });
      adVideo.muted = true;
      const skip = h("button", { type: "button", class: "btn sm", disabled: true, text: `Skip ad in ${ad.skip_after}` });
      const unmute = h("button", { type: "button", class: "btn ghost sm", text: "Sound on", style: { color: "#fff" }, onclick: () => { adVideo.muted = !adVideo.muted; unmute.textContent = adVideo.muted ? "Sound on" : "Sound off"; } });
      const bar = h("div", { class: "vid-ad-bar" },
        h("div", { class: "vid-row" }, h("span", { class: "vid-ad-label", text: "Ad" }), h("span", { text: `${ad.advertiser} · ${ad.headline}` })),
        h("div", { class: "vid-row" }, unmute, adLink(ad), skip));
      const holder = h("div", { class: "vid-ad", role: "region", "aria-label": "Advertisement" }, adVideo, bar);
      const done = () => { if (finished) return; finished = true; holder.remove(); resolve(); };
      adVideo.addEventListener("timeupdate", () => {
        if (!reported && adVideo.currentTime >= ad.seen_after) { reported = true; seenAd(ad); }
        const left = Math.ceil(ad.skip_after - adVideo.currentTime);
        skip.disabled = left > 0;
        skip.textContent = left > 0 ? `Skip ad in ${left}` : "Skip ad";
      });
      skip.addEventListener("click", done);
      adVideo.addEventListener("ended", done);
      adVideo.addEventListener("error", done);
      setTimeout(() => { if (!reported && adVideo.currentTime === 0) done(); }, 10000); // an ad that cannot start never blocks the video
      box.prepend(holder);
      adVideo.play().catch(() => null);
    });
  }

  // A banner beside the video; it counts after two seconds on screen.
  function bannerNode(ad) {
    const node = h("aside", { class: "vid-banner", "aria-label": "Sponsored" },
      ad.media_url ? h("img", { src: ad.media_url, alt: ad.headline }) : h("span", {}),
      h("div", { style: { minWidth: "0" } }, h("span", { class: "vid-meta", text: `Sponsored · ${ad.advertiser}` }), h("div", { style: { fontWeight: "700" }, text: ad.headline }), ad.body ? h("div", { text: ad.body }) : null, h("span", { class: "vid-meta", text: ad.domain })),
      adLink(ad, "primary sm"));
    let timer = 0, reported = false;
    if (typeof IntersectionObserver !== "undefined") {
      const observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (reported) continue;
          if (entry.isIntersecting && entry.intersectionRatio >= 0.5) { if (!timer) timer = setTimeout(() => { reported = true; observer.disconnect(); seenAd(ad); }, ad.seen_after * 1000 + 100); }
          else if (timer) { clearTimeout(timer); timer = 0; }
        }
      }, { threshold: [0, 0.5, 1] });
      observer.observe(node);
    }
    return node;
  }

  // Where the person is in a video and how long they really watched (seeking does not count).
  function track(videoEl, videoId) {
    let watched = 0, last = null;
    const send = () => api(`/videos/${videoId}/progress`, { method: "POST", body: { position: videoEl.currentTime || 0, watched }, silent: true }).catch(() => null);
    videoEl.addEventListener("timeupdate", () => {
      const now = videoEl.currentTime;
      if (last !== null && !videoEl.paused) { const step = now - last; if (step > 0 && step < 2) watched += step; }
      last = now;
    });
    videoEl.addEventListener("seeking", () => { last = null; });
    videoEl.addEventListener("play", send, { once: true });
    videoEl.addEventListener("pause", send);
    videoEl.addEventListener("ended", send);
    const timer = setInterval(() => { if (!videoEl.isConnected) { clearInterval(timer); return; } if (!videoEl.paused) send(); }, 10000);
  }

  function player(video) {
    const box = h("div", { class: `vid-player${video.kind === "reel" || video.height > video.width ? " tall" : ""}` });
    const el = h("video", { controls: true, playsinline: true, preload: "metadata", poster: video.thumb_url || undefined, "aria-label": video.title });
    if (video.captions_url) el.append(h("track", { kind: "captions", src: video.captions_url, srclang: video.language || "en", label: "Captions", default: true }));
    box.append(el);
    return { box, el };
  }

  // ---------------------------------------------------------------- the watch page
  async function watchPage(main, info, id) {
    const { video, support, membership } = await api(`/videos/${id}`);
    const body = S.page(main, { title: video.title, subtitle: `${video.channel.name} · ${viewsText(video.views)} · ${video.scheduled ? `goes out ${lower(outText(video.created_at))}` : when(video.created_at)}`, actions: [S.button("Back to Videos", () => go({}), { kind: "ghost" })] });
    body.classList.add("vid-page");
    if (video.blocked) body.append(h("div", { class: "card", role: "status", style: { borderColor: "var(--warning)", padding: "12px" }, text: `${BLOCKED[video.blocked] || "Not shown to others"}. Only you can watch it.` }));
    if (video.scheduled) body.append(h("div", { class: "card vid-notice", role: "status", text: `This video goes out ${lower(outText(video.created_at))}. Until then only you can watch it.` }));
    if (video.locked) {
      // A members-only video for someone who has not joined: its picture and how to join, no player.
      body.append(lockedNode(video, membership, info));
      body.append(h("div", { class: "card suite-section" }, h("div", { class: "vid-row" }, face(video.channel), channelLink(video.channel)), video.description ? h("p", { style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: "0" }, text: video.description }) : null));
      return;
    }
    const { box, el } = player(video);
    const note = h("p", { class: "muted", hidden: true });
    if (video.mime && el.canPlayType && el.canPlayType(video.mime) === "") { note.hidden = false; note.textContent = `This browser may not play this file. ${video.playback || ""}`; }
    // Carry on where the person stopped last time (they can start again from the beginning).
    const resume = video.position ? h("div", { class: "vid-row vid-resume", role: "status" },
      h("span", { text: `Carrying on from ${clockText(video.position)}, where you stopped.` }),
      S.button("Start from the beginning", () => { el.currentTime = 0; resume.remove(); el.play().catch(() => null); }, { kind: "ghost sm" })) : null;
    body.append(...[box, note, resume].filter(Boolean)); // a native append() writes "null" for a missing node
    track(el, video.id);
    const start = async () => {
      if (!video.mine && video.ads_enabled) {
        const ad = await serveAd(video.id, "preroll");
        if (ad && ad.media_url) { el.hidden = true; await playPreroll(box, ad); el.hidden = false; }
      }
      if (video.position) el.addEventListener("loadedmetadata", () => { if (el.currentTime < 1) el.currentTime = video.position; }, { once: true });
      el.src = video.stream_url;
      el.play().catch(() => null);
    };
    start();
    if (!video.mine && video.ads_enabled) serveAd(video.id, "banner").then((ad) => { if (ad) box.after(bannerNode(ad)); });

    const likeLabel = () => `${video.liked ? "Liked" : "Like"} (${video.likes})`;
    const likeButton = toggle(likeLabel(), async (set) => {
      const result = await api(`/videos/${video.id}/like`, { method: "POST", body: { on: !video.liked } });
      video.liked = result.liked; video.likes = result.likes;
      set(likeLabel(), video.liked);
    }, { kind: "ghost", pressed: !!video.liked });
    const subscribeButton = video.mine ? null : toggle(video.subscribed ? "Subscribed" : "Subscribe", async (set) => {
      const result = await api(`/channels/${video.channel.id}/subscribe`, { method: "POST", body: { on: !video.subscribed } });
      video.subscribed = result.subscribed;
      set(video.subscribed ? "Subscribed" : "Subscribe", video.subscribed);
      subscribeButton.className = `btn ${video.subscribed ? "secondary" : "primary"}`;
      subscribers.textContent = subscribersText(result.subscribers);
    }, { kind: video.subscribed ? "secondary" : "primary", pressed: !!video.subscribed });
    const saveButton = toggle(video.saved ? "Saved for later" : "Watch later", async (set) => {
      const result = await api(`/videos/${video.id}/later`, { method: "POST", body: { on: !video.saved } });
      video.saved = result.saved;
      set(video.saved ? "Saved for later" : "Watch later", video.saved);
    }, { kind: "ghost", pressed: !!video.saved });
    const subscribers = h("div", { class: "vid-meta", text: subscribersText(video.subscribers) });
    body.append(h("div", { class: "card suite-section" },
      h("div", { class: "vid-row", style: { justifyContent: "space-between" } },
        h("div", { class: "vid-row" }, face(video.channel), h("div", {}, channelLink(video.channel), subscribers)),
        h("div", { class: "vid-row" }, support && support.can_give ? S.button("Support", () => supportDialog(video.channel, support, video.id), { kind: "secondary" }) : null, subscribeButton)),
      h("div", { class: "vid-row" }, likeButton, saveButton,
        S.button("Add to playlist", () => addToPlaylist(video), { kind: "ghost" }),
        S.button("Share", () => share(video), { kind: "ghost" }),
        video.mine ? null : S.button("Report", () => report(info, "video", video.id), { kind: "ghost" })),
      video.paid_promotion ? h("p", { class: "vid-promo", text: "Includes paid promotion: the creator was paid by a brand for part of this video." }) : null,
      h("div", { class: "vid-chips" }, h("span", { class: "suite-pill", text: RATING[video.rating] }), h("span", { class: "suite-pill", text: CATEGORY[video.category] || video.category }), video.kind !== "video" ? h("span", { class: "suite-pill", text: KIND[video.kind] }) : null, video.genre ? h("span", { class: "suite-pill", text: GENRE[video.genre] || video.genre }) : null, video.members_only ? h("span", { class: "suite-pill info", text: "For members" }) : null),
      chaptersNode(video, el),
      video.description ? h("p", { style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: "0" }, text: video.description }) : null,
      video.credits && video.credits.length ? h("dl", { class: "kv" }, ...video.credits.flatMap((item) => [h("dt", { text: item.role || "Credit" }), h("dd", { text: item.name })])) : null,
      video.tags && video.tags.length ? h("p", { class: "vid-meta", text: video.tags.map((tag) => `#${tag}`).join(" ") }) : null,
      h("p", { class: "vid-meta", text: `Video id: ${video.id}` })));
    if (membership && !video.mine) body.append(...[membershipSection(video.channel, membership, info)].filter(Boolean));
    if (support) body.append(supportSection(video.channel, support, video.id));
    body.append(await commentsSection(info, video));
    try {
      const more = await api(`/home?category=${encodeURIComponent(video.category)}`);
      const next = [...more.popular, ...more.recent].filter((item, index, all) => item.id !== video.id && all.findIndex((other) => other.id === item.id) === index).slice(0, 8);
      if (next.length) body.append(S.section("Up next", grid(next, ["", ""])));
    } catch { /* the list is extra */ }
  }

  // Chapters written in the description ("0:00 Intro"): a button for each moment; the one playing is marked.
  function chaptersNode(video, el) {
    if (!video.chapters || !video.chapters.length) return null;
    const buttons = video.chapters.map((chapter) => h("button", { type: "button", class: "vid-chapter", onclick: () => { el.currentTime = chapter.at; el.play().catch(() => null); } },
      h("span", { class: "vid-chapter-time", text: clockText(chapter.at) }), h("span", { text: chapter.title })));
    el.addEventListener("timeupdate", () => {
      let current = 0;
      video.chapters.forEach((chapter, index) => { if (el.currentTime >= chapter.at) current = index; });
      buttons.forEach((button, index) => { if (index === current) button.setAttribute("aria-current", "true"); else button.removeAttribute("aria-current"); });
    });
    return h("nav", { class: "vid-chapters", "aria-label": "Chapters" }, h("strong", { text: "Chapters" }), h("div", { class: "vid-chapter-list" }, ...buttons));
  }

  // ---------------------------------------------------------------- support from viewers
  function supportSection(channel, support, videoId) {
    const names = support.supporters || [];
    return S.section(`Support ${channel.name}`,
      h("p", { class: "muted", text: support.note || `${channel.name} takes support from viewers. You pay them directly; Monosage takes nothing.` }),
      names.length ? h("p", { class: "vid-meta", text: `Thank you to ${names.map((item) => item.name).join(", ")}.` }) : null,
      support.can_give ? h("div", { class: "btn-row" }, S.button(`Support ${channel.name}`, () => supportDialog(channel, support, videoId), { kind: "secondary" })) : h("p", { class: "muted", text: "Supporting creators with money is for adults." }));
  }

  function supportDialog(channel, support, videoId) {
    return S.edit({
      title: `Support ${channel.name}`,
      fields: [
        { name: "amount_cents", label: `Amount (${support.currency})`, type: "money", required: true, hint: "You pay the creator yourself, on their own payment page or by UPI." },
        { name: "message", label: "A message for them (optional)", type: "textarea", rows: 2 },
        { name: "show_name", label: "Thank me by name on the channel", type: "checkbox" },
      ],
      values: { amount_cents: 500, show_name: true },
      submitLabel: "Continue to pay",
      onSave: async (values) => {
        const result = await api(`/channels/${channel.id}/support`, { method: "POST", body: { ...values, video_id: videoId || "" } });
        payDialog(result.tip, result.pay, "You can withdraw the pledge from Library until then.");
      },
    });
  }

  // Where and how to pay a creator (support or a membership): the reference, their page, their UPI id.
  function payDialog(item, pay, after) {
    const parts = [
      h("p", { text: `Send ${item.amount} to ${pay.name}. Put this reference in the payment note so they know it is from you:` }),
      h("div", { class: "vid-row" }, h("code", { class: "vid-code", text: item.reference }), S.button("Copy", () => S.copy(item.reference), { kind: "secondary sm" })),
    ];
    if (pay.link) parts.push(h("div", { class: "btn-row" }, h("a", { class: "btn primary", href: pay.link, target: "_blank", rel: "noopener noreferrer", text: "Open their payment page" })));
    if (pay.upi) {
      parts.push(h("div", { class: "vid-row" }, h("span", { text: "UPI id:" }), h("code", { class: "vid-code", text: pay.upi }), S.button("Copy", () => S.copy(pay.upi), { kind: "secondary sm" })));
      if (pay.upi_link) parts.push(h("div", { class: "btn-row" }, h("a", { class: `btn ${pay.link ? "secondary" : "primary"}`, href: pay.upi_link, text: "Pay with a UPI app" }), h("span", { class: "muted", text: "On a phone with a UPI app." })));
    }
    parts.push(h("p", { class: "muted", text: `${pay.name} marks it received when the money arrives, and you are told. ${after}` }));
    Aira.modal({ title: "How to pay", body: h("div", { class: "suite-body" }, ...parts), actions: [{ label: "Done", kind: "primary" }] });
  }

  // ---------------------------------------------------------------- memberships (a creator's members-only videos)
  const monthsText = (n) => (Number(n) === 1 ? "1 month" : `${n} months`);
  const outText = (iso) => `on ${S.when(iso)}`;

  function joinDialog(channel, membership) {
    return S.edit({
      title: membership.member ? `Renew your membership of ${channel.name}` : `Join ${channel.name}`,
      fields: [{ name: "months", label: "For how long?", type: "select", options: membership.months.map((n) => [String(n), `${monthsText(n)}: ${S.money(membership.price_cents * n, membership.currency)}`]) }],
      values: { months: "1" },
      submitLabel: "Continue to pay",
      onSave: async (values) => {
        const result = await api(`/channels/${channel.id}/join`, { method: "POST", body: { months: Number(values.months) } });
        payDialog(result.payment, result.pay, "You can withdraw it from Library until then.");
        Aira.router.render();
      },
    });
  }

  // What a person who has not joined sees in place of a members-only video.
  function lockedNode(video, membership, info) {
    const box = h("div", { class: `card vid-locked${video.thumb_url ? "" : " no-picture"}` },
      video.thumb_url ? h("img", { src: video.thumb_url, alt: "" }) : null,
      h("div", { class: "suite-body" },
        h("h2", { class: "suite-h2", text: `For members of ${video.channel.name}` }),
        h("p", { text: "This video is for the channel's members. Members pay the creator directly each month and can watch all their videos for members." })));
    const inner = box.lastChild;
    if (!membership || !membership.open) inner.append(h("p", { class: "muted", text: "This channel is not taking new members right now." }));
    else if (!adult(info.channel)) inner.append(h("p", { class: "muted", text: "Memberships are for adults." }));
    else {
      inner.append(h("p", { class: "vid-price", text: `${membership.price} a month` }));
      if (membership.perks) inner.append(h("p", { class: "muted", style: { whiteSpace: "pre-wrap" }, text: membership.perks }));
      if (membership.waiting) inner.append(h("p", { role: "status", text: `Your payment of ${membership.waiting.amount} (reference ${membership.waiting.reference}) is waiting for ${video.channel.name} to confirm it.` }));
      else if (membership.can_join) inner.append(h("div", { class: "btn-row" }, S.button(`Join for ${membership.price} a month`, () => joinDialog(video.channel, membership), { kind: "primary" })));
    }
    return box;
  }

  function membershipSection(channel, membership, info) {
    const parts = [];
    if (membership.member) parts.push(h("p", { text: `You are a member until ${S.day(membership.paid_until)}. You can watch all of ${channel.name}'s videos for members.` }));
    else if (membership.open) parts.push(h("p", { text: `${membership.price} a month. Members watch the channel's videos for members and support the creator every month.` }));
    if (membership.perks && membership.open) parts.push(h("p", { class: "muted", style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere" }, text: membership.perks }));
    if (membership.waiting) {
      parts.push(h("div", { class: "vid-row" }, h("span", { role: "status", text: `Your payment of ${membership.waiting.amount} (reference ${membership.waiting.reference}) is waiting for ${channel.name} to confirm it.` }),
        S.button("Withdraw", async () => { if (await Aira.confirm({ title: "Withdraw this payment?", message: "Do this only if you did not send the money.", confirmLabel: "Withdraw" })) { await api(`/memberships/payments/${membership.waiting.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" })));
    } else if (membership.can_join) parts.push(h("div", { class: "btn-row" }, S.button(membership.member ? "Renew membership" : `Join for ${membership.price} a month`, () => joinDialog(channel, membership), { kind: membership.member ? "secondary" : "primary" })));
    else if (membership.open && !adult(info.channel)) parts.push(h("p", { class: "muted", text: "Memberships are for adults." }));
    if (!parts.length) return null;
    return S.section(membership.member ? `You are a member of ${channel.name}` : `Become a member of ${channel.name}`, ...parts);
  }

  async function commentsSection(info, video) {
    const section = S.section(`Comments (${video.comments})`);
    const list = h("div", { class: "suite-body" });
    const draw = async () => {
      const { comments } = await api(`/videos/${video.id}/comments`);
      list.replaceChildren(...(comments.length ? comments.map((item) => h("div", { class: "vid-comment" }, face(item.author),
        h("div", { style: { flex: "1", minWidth: "0" } }, h("strong", { text: item.author.name }), h("span", { class: "vid-meta", text: ` · ${when(item.created_at)}` }), h("p", { text: item.text })),
        item.can_delete ? S.button("Delete", async () => { await api(`/comments/${item.id}`, { method: "DELETE" }); await draw(); }, { kind: "ghost sm" }) : null,
        item.mine ? null : S.button("Report", () => report(info, "comment", item.id), { kind: "ghost sm" }))) : [h("p", { class: "muted", text: "No comments yet." })]));
    };
    if (video.can_comment) {
      const input = h("textarea", { rows: "2", maxlength: "1000", placeholder: "Add a comment", "aria-label": "Your comment" });
      section.append(input, h("div", { class: "btn-row" }, S.button("Comment", async () => {
        if (!input.value.trim()) return;
        await api(`/videos/${video.id}/comments`, { method: "POST", body: { text: input.value } });
        input.value = "";
        await draw();
      }, { kind: "secondary" })));
    } else if (info.channel.age_group === "child") section.append(h("p", { class: "muted", text: "Comments are for older people. You can like the video and save it." }));
    section.append(list);
    await draw();
    return section;
  }

  async function addToPlaylist(video) {
    const { playlists } = await api("/playlists");
    const list = h("div", { class: "suite-body" });
    const name = h("input", { type: "text", maxlength: "80", placeholder: "New playlist name", "aria-label": "New playlist name" });
    const dialog = Aira.modal({ title: "Add to a playlist", body: h("div", { class: "suite-body" }, list, h("div", { class: "vid-row" }, name, S.button("Create and add", async () => {
      if (!name.value.trim()) return;
      const made = (await api("/playlists", { method: "POST", body: { name: name.value } })).playlist;
      await api(`/playlists/${made.id}/videos/${video.id}`, { method: "POST", body: {} });
      Aira.toast(`Added to ${made.name}.`, "success");
      dialog.close();
    }, { kind: "secondary" }))), actions: [{ label: "Close", kind: "ghost" }] });
    list.replaceChildren(...(playlists.length ? playlists.map((item) => S.button(`${item.name} (${item.count})`, async () => { await api(`/playlists/${item.id}/videos/${video.id}`, { method: "POST", body: {} }); Aira.toast(`Added to ${item.name}.`, "success"); dialog.close(); }, { kind: "ghost" })) : [h("p", { class: "muted", text: "You have no playlists yet. Name one below." })]));
  }

  async function share(video) {
    const inApp = `${location.origin}/#/videos?watch=${video.id}`;
    const parts = [h("p", { class: "muted", text: "People who use Monosage can open this address." }), h("div", { class: "vid-row" }, h("code", { style: { overflowWrap: "anywhere" }, text: inApp }), S.button("Copy", () => S.copy(inApp), { kind: "secondary sm" }))];
    if (video.mine && video.members_only) parts.push(h("p", { class: "muted", text: "Videos for members have no page for everyone: only your members can watch them, in Monosage." }));
    else if (video.mine && video.scheduled) parts.push(h("p", { class: "muted", text: "Once the video is out you can also make a page anyone can watch." }));
    else if (video.mine && video.visibility !== "private" && video.rating !== "mature" && !video.blocked) {
      const { link } = await api(`/videos/${video.id}/link`);
      parts.push(S.share({
        title: "A page anyone can watch",
        text: "Make a link that plays this video for anyone, without an account. You can turn it off any time.",
        link,
        onCreate: async () => (await api(`/videos/${video.id}/link`, { method: "POST", body: {} })).link,
        onRevoke: () => api(`/videos/${video.id}/link`, { method: "DELETE" }),
      }));
    }
    Aira.modal({ title: "Share this video", body: h("div", { class: "suite-body" }, ...parts), actions: [{ label: "Done", kind: "primary" }] });
  }

  // ---------------------------------------------------------------- home, search, films, channel, playlist
  // Results for the search box across the top (Enter there, or ?q=): videos, reels and short films everyone can
  // find, the channels, and the person's own videos that only they can find (private, link only, scheduled).
  async function searchResults(panel, info, route) {
    const q = String(route.query.q || "").slice(0, 100);
    const kind = Object.prototype.hasOwnProperty.call(KIND, route.query.kind || "") ? route.query.kind : "";
    panel.append(h("div", { class: "vid-row", style: { justifyContent: "space-between" } },
      h("div", { class: "vid-chips", role: "group", "aria-label": "Show" },
        ...[["", "Everything"], ["video", "Videos"], ["reel", "Reels"], ["short", "Short films"]].map(([id, label]) => h("button", { type: "button", class: `btn sm ${kind === id ? "secondary" : "ghost"}`, "aria-pressed": String(kind === id), text: label, onclick: (event) => goFrom(event, id ? { tab: "home", q, kind: id } : { tab: "home", q }) }))),
      h("a", { class: "btn ghost sm", href: "#/videos?tab=home", text: "Clear the search" })));
    const [found, mine] = await Promise.all([api(`/search?q=${encodeURIComponent(q)}${kind ? `&kind=${kind}` : ""}`), api(`/studio?q=${encodeURIComponent(q)}`, { silent: true }).catch(() => ({ videos: [] }))]);
    const listed = new Set(found.videos.map((video) => video.id));
    const own = (mine.videos || []).filter((video) => !listed.has(video.id) && (!kind || video.kind === kind)).map((video) => ({ ...video, channel: video.channel || info.channel }));
    if (found.channels.length && !kind) panel.append(S.section("Channels", h("div", { class: "vid-chips" }, ...found.channels.map((channel) => h("a", { class: "btn ghost", href: `#/videos?channel=${channel.id}` }, h("span", { text: `${channel.avatar} ${channel.name}` }), h("span", { class: "vid-meta", text: ` · ${subscribersText(channel.subscribers)}` }))))));
    const nothing = !found.videos.length && !own.length && !(found.channels.length && !kind);
    if (nothing) { panel.append(S.section("", Aira.ui.empty(`Nothing in Videos matches “${q}”`, kind ? "Try Everything, or other words." : "Try other words, or look through the categories on Home.", homeLink("Back to Home")))); return; }
    if (found.videos.length || !own.length) panel.append(S.section(found.videos.length === 1 ? "1 video" : `${found.videos.length} videos`, grid(found.videos, ["No videos match", kind ? "Try Everything, or other words." : "Try other words."])));
    if (own.length) panel.append(S.section("Your videos only you can find", h("p", { class: "muted", text: "Private, link-only or scheduled videos of yours that match." }), grid(own, ["", ""], (video) => `${VISIBILITY[video.visibility] || ""}${video.scheduled ? " · scheduled" : ""} · ${viewsText(video.views)}`), h("div", { class: "btn-row" }, h("a", { class: "btn ghost sm", href: `#/videos?tab=studio&q=${encodeURIComponent(q)}`, text: "Manage them in Studio" }))));
  }

  async function homeTab(panel, info, route) {
    if (route.query.q) { await searchResults(panel, info, route); return; }
    const category = route.query.category || "";
    const chips = h("div", { class: "vid-chips vid-chipbar", role: "group", "aria-label": "Categories" },
      h("button", { type: "button", class: `btn sm ${category ? "ghost" : "secondary"}`, "aria-pressed": String(!category), text: "All", onclick: (event) => goFrom(event, { tab: "home" }) }),
      ...info.categories.map((id) => h("button", { type: "button", class: `btn sm ${category === id ? "secondary" : "ghost"}`, "aria-pressed": String(category === id), text: CATEGORY[id] || id, onclick: (event) => goFrom(event, { tab: "home", category: id }) })));
    panel.append(chips);
    const data = await api(`/home${category ? `?category=${category}` : ""}`);
    if (data.continue && data.continue.length) panel.append(S.section("Continue watching", grid(data.continue, ["", ""], (video) => `${video.channel.name} · stopped at ${clockText(video.position)}`)));
    if (data.subscriptions.length) panel.append(S.section("From your subscriptions", grid(data.subscriptions, ["", ""])));
    if (data.reels.length) panel.append(S.section("Reels", h("div", { class: "vid-grid vid-shorts" }, ...data.reels.map((reel) => card(reel, { open: () => go({ tab: "reels", start: reel.id }) })))));
    // Nothing at all yet (a new household, or a category nobody has used): one invitation, not two empty lists.
    if (!data.popular.length && !data.recent.length && !data.reels.length && !data.subscriptions.length) {
      panel.append(S.section("", Aira.ui.empty(category ? `No ${lower(CATEGORY[category] || category)} videos yet` : "No videos yet", category ? "Be the first to share one, or look at All." : "Be the first: share a video, a reel or a short film. Everyone in Monosage can watch it, and you can earn from it.", uploadLink(info, "Upload a video"))));
      return;
    }
    panel.append(S.section("Popular", grid(data.popular, ["Nothing popular yet", "Videos people watch most appear here.", uploadLink(info, "Upload a video")]), h("p", { class: "vid-meta", text: `How Popular is worked out: ${data.ranking}. Nothing is paid for and nothing is personal.` })));
    panel.append(S.section("New", grid(data.recent, ["Nothing new yet", "Videos people upload appear here."])));
  }

  async function filmsTab(panel, info, route) {
    const genre = route.query.genre || "";
    const data = await api(`/films${genre ? `?genre=${genre}` : ""}`);
    panel.append(h("div", { class: "vid-chips vid-chipbar", role: "group", "aria-label": "Genres" },
      h("button", { type: "button", class: `btn sm ${genre ? "ghost" : "secondary"}`, "aria-pressed": String(!genre), text: "All", onclick: (event) => goFrom(event, { tab: "films" }) }),
      ...data.genres.map((id) => h("button", { type: "button", class: `btn sm ${genre === id ? "secondary" : "ghost"}`, "aria-pressed": String(genre === id), text: GENRE[id] || id, onclick: (event) => goFrom(event, { tab: "films", genre: id }) }))));
    panel.append(S.section(genre ? GENRE[genre] || genre : "Every genre", grid(data.films, [genre ? `No ${lower(GENRE[genre] || genre)} films yet` : "No short films yet", "Share one: choose its genre and add its credits.", uploadLink(info, "Upload a short film", "short")], (film) => `${film.channel.name} · ${GENRE[film.genre] || ""} · ${clockText(film.duration)}`)));
  }

  async function channelPage(main, info, id) {
    const data = await api(`/channels/${id}`);
    const channel = data.channel;
    const body = S.page(main, { title: channel.name, subtitle: subscribersText(channel.subscribers), actions: [S.button("Back to Videos", () => go({}), { kind: "ghost" })] });
    body.classList.add("vid-page");
    const subscribe = channel.mine ? null : toggle(channel.subscribed ? "Subscribed" : "Subscribe", async (set) => {
      const result = await api(`/channels/${channel.id}/subscribe`, { method: "POST", body: { on: !channel.subscribed } });
      channel.subscribed = result.subscribed;
      set(channel.subscribed ? "Subscribed" : "Subscribe", channel.subscribed);
      subscribe.className = `btn ${channel.subscribed ? "secondary" : "primary"}`;
    }, { kind: channel.subscribed ? "secondary" : "primary", pressed: !!channel.subscribed });
    body.append(S.section("", h("div", { class: "vid-row" }, face(channel), h("strong", { text: channel.name }), subscribe, channel.mine ? null : S.button("Report", () => report(info, "channel", channel.id), { kind: "ghost sm" })), channel.about ? h("p", { style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere" }, text: channel.about }) : null));
    if (data.membership) body.append(...[membershipSection(channel, data.membership, info)].filter(Boolean));
    if (data.support) body.append(supportSection(channel, data.support, ""));
    body.append(S.section("Videos", grid(data.videos, ["No videos yet", "This channel has not shared anything yet."])));
  }

  async function playlistPage(main, info, id) {
    const { playlist } = await api(`/playlists/${id}`);
    const body = S.page(main, { title: playlist.name, subtitle: playlist.count === 1 ? "1 video" : `${playlist.count} videos`, actions: [
      S.button("Rename", () => S.edit({ title: "Rename playlist", fields: [{ name: "name", label: "Name", required: true }], values: playlist, onSave: async (values) => { await api(`/playlists/${playlist.id}`, { method: "PATCH", body: values }); Aira.router.render(); } }), { kind: "ghost" }),
      S.button("Delete", async () => { if (await S.confirmDelete("this playlist")) { await api(`/playlists/${playlist.id}`, { method: "DELETE" }); go({ tab: "library" }); } }, { kind: "ghost" }),
      S.button("Back to Library", () => go({ tab: "library" }), { kind: "ghost" })] });
    body.classList.add("vid-page");
    body.append(playlist.videos.length ? h("div", { class: "vid-grid" }, ...playlist.videos.map((video) => h("div", { class: "suite-body", style: { gap: "6px" } }, card(video), S.button("Remove", async () => { await api(`/playlists/${playlist.id}/videos/${video.id}`, { method: "DELETE" }); Aira.router.render(); }, { kind: "ghost sm" })))) : Aira.ui.empty("This playlist is empty", "Open a video and choose Add to playlist.", homeLink("Find videos")));
  }

  const newPlaylist = () => S.edit({ title: "New playlist", fields: [{ name: "name", label: "Name", required: true }], submitLabel: "Create playlist", onSave: async (values) => { const made = (await api("/playlists", { method: "POST", body: values })).playlist; answers.clear(); go({ playlist: made.id }); } });

  async function libraryTab(panel, info) {
    const [history, later, lists, subs, given, joined] = await Promise.all([api("/history"), api("/later"), api("/playlists"), api("/subscriptions"), adult(info.channel) ? api("/support/given") : Promise.resolve({ tips: [] }), adult(info.channel) ? api("/memberships/mine") : Promise.resolve({ memberships: [] })]);
    const membershipsNode = !joined.memberships.length ? null : S.section("Your memberships", h("ul", { class: "life-lines" }, ...joined.memberships.map((item) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
        h("div", { style: { flex: "1 1 220px", minWidth: "0" } },
          h("a", { class: "life-line-text", href: `#/videos?channel=${item.channel.id}`, text: `${item.channel.avatar} ${item.channel.name}` }),
          h("span", { class: "life-line-sub muted", text: [item.active ? `Member until ${S.day(item.paid_until)}` : item.paid_until ? `Ended ${S.day(item.paid_until)}` : "", item.waiting ? `payment of ${item.waiting.amount} (reference ${item.waiting.reference}) waiting to be confirmed` : ""].filter(Boolean).join(" · ") })),
        item.waiting ? S.button("Withdraw", async () => { if (await Aira.confirm({ title: "Withdraw this payment?", message: "Do this only if you did not send the money.", confirmLabel: "Withdraw" })) { await api(`/memberships/payments/${item.waiting.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) : null,
        !item.waiting && item.can_renew ? h("a", { class: "btn ghost sm", href: `#/videos?channel=${item.channel.id}`, text: item.active ? "Renew" : "Join again" }) : null))));
    panel.append(S.section("History", history.videos.length ? h("div", { class: "btn-row" }, S.button("Clear history", async () => { if (await Aira.confirm({ title: "Clear your history?", message: "Videos you watched are removed from this list. It does not change any view counts.", confirmLabel: "Clear" })) { await api("/history", { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" })) : null,
      grid(history.videos, ["Nothing watched yet", "Videos you watch appear here, so you can carry on where you stopped.", homeLink("Find something to watch")], (video) => `${video.channel.name} · watched ${lower(when(video.watched_at))}`)));
    panel.append(S.section("Watch later", grid(later.videos, ["Nothing saved", "Press Watch later under a video to keep it here."])));
    const newList = S.button("New playlist", () => newPlaylist(), { kind: "secondary sm" });
    panel.append(S.section("Playlists", h("div", { class: "btn-row" }, newList), lists.playlists.length ? h("div", { class: "vid-chips" }, ...lists.playlists.map((item) => h("a", { class: "btn ghost", href: `#/videos?playlist=${item.id}`, text: `${item.name} (${item.count})` }))) : h("p", { class: "muted", text: "Make playlists to keep videos together." })));
    panel.append(S.section("Subscriptions", subs.channels.length ? h("div", { class: "vid-chips" }, ...subs.channels.map((channel) => h("a", { class: "btn ghost", href: `#/videos?channel=${channel.id}`, text: `${channel.avatar} ${channel.name}` }))) : h("p", { class: "muted", text: "Subscribe to channels you like to see their new videos first." })));
    if (membershipsNode) panel.append(membershipsNode);
    if (given.tips.length) {
      panel.append(S.section("Creators you support", h("ul", { class: "life-lines" }, ...given.tips.map((tip) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
        h("div", { style: { flex: "1 1 220px", minWidth: "0" } },
          h("span", { class: "life-line-text", text: `${tip.amount} to ${tip.channel.name}` }),
          h("span", { class: "life-line-sub muted", text: `${TIP_STATUS[tip.status] || tip.status} · reference ${tip.reference} · ${lower(when(tip.created_at))}` })),
        tip.status === "pledged" ? S.button("Withdraw", async () => { if (await Aira.confirm({ title: "Withdraw this pledge?", message: "Do this only if you did not send the money.", confirmLabel: "Withdraw" })) { await api(`/support/given/${tip.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) : null)))));
    }
  }

  // ---------------------------------------------------------------- reels: one tall video at a time
  async function reelsTab(panel, info, route) {
    const { reels } = await api(`/reels${route.query.start ? `?start=${route.query.start}` : ""}`);
    if (!reels.length) { panel.append(S.section("", Aira.ui.empty("No reels yet", `A reel is a tall video of up to ${info.limits.reel_seconds} seconds, filmed on a phone held upright.`, uploadLink(info, "Upload a reel", "reel")))); return; }
    const items = reels.map((video) => ({ video }));
    const stage = h("div", { class: "vid-reels", tabindex: "0", "aria-label": "Reels. Use the arrow keys or swipe to move between them." });
    const counter = h("span", { class: "vid-meta", "aria-live": "polite" });
    let index = 0, muted = true, lastMove = 0;
    const sponsored = new Set();
    const show = async (next) => {
      index = Math.max(0, Math.min(items.length - 1, next));
      // every fourth reel may be a sponsored one, chosen for the reel before it
      if (index > 0 && index % 4 === 0 && !sponsored.has(index) && !items[index].ad) {
        sponsored.add(index);
        const ad = await serveAd(items[index - 1].video.id, "reel");
        if (ad && ad.media_url) items.splice(index, 0, { ad });
      }
      const item = items[index];
      const likesNode = h("span", { text: item.video ? `${viewsText(item.video.views)} · ${likesText(item.video.likes)}` : "" });
      const el = h("video", { src: item.ad ? item.ad.media_url : item.video.stream_url, autoplay: true, loop: !item.ad, playsinline: true, "aria-label": item.ad ? `Sponsored reel from ${item.ad.advertiser}` : item.video.title });
      el.muted = muted;
      const info_ = item.ad
        ? h("div", { class: "vid-reel-info" }, h("div", { class: "vid-row" }, h("span", { class: "vid-ad-label", text: "Sponsored" }), h("strong", { text: item.ad.advertiser })), h("span", { text: item.ad.headline }), h("div", {}, adLink(item.ad, "primary sm")))
        : h("div", { class: "vid-reel-info" }, h("strong", { text: item.video.title }), h("a", { href: `#/videos?channel=${item.video.channel.id}`, text: item.video.channel.name }), likesNode);
      const reel = h("div", { class: "vid-reel" }, el, info_);
      if (item.ad) {
        let reported = false;
        el.addEventListener("timeupdate", () => { if (!reported && el.currentTime >= item.ad.seen_after) { reported = true; seenAd(item.ad); } });
      } else track(el, item.video.id);
      const soundButton = toggle(muted ? "Sound on" : "Sound off", (set) => { muted = !muted; el.muted = muted; set(muted ? "Sound on" : "Sound off", !muted); }, { kind: "ghost", pressed: !muted });
      const actions = h("div", { class: "vid-reel-actions" },
        S.button("Previous", () => show(index - 1), { kind: "ghost" }),
        soundButton,
        item.ad ? null : toggle(item.video.liked ? "Liked" : "Like", async (set) => { const result = await api(`/videos/${item.video.id}/like`, { method: "POST", body: { on: !item.video.liked } }); item.video.liked = result.liked; item.video.likes = result.likes; set(result.liked ? "Liked" : "Like", result.liked); likesNode.textContent = `${viewsText(item.video.views)} · ${likesText(item.video.likes)}`; }, { kind: "ghost", pressed: !!item.video.liked }),
        item.ad ? null : S.button("Open", () => go({ watch: item.video.id }), { kind: "ghost" }),
        item.ad ? null : S.button("Report", () => report(info, "video", item.video.id), { kind: "ghost" }),
        S.button("Next", () => show(index + 1), { kind: "primary" }));
      counter.textContent = `${index + 1} of ${items.length}`;
      stage.replaceChildren(reel, actions, counter);
      el.play().catch(() => null);
    };
    const move = (step) => { const now = Date.now(); if (now - lastMove < 500) return; lastMove = now; show(index + step); };
    const keys = (event) => {
      if (!stage.isConnected) { document.removeEventListener("keydown", keys); return; }
      if (event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
      if (event.key === "ArrowDown" || event.key === "j" || event.key === "PageDown") { event.preventDefault(); move(1); }
      if (event.key === "ArrowUp" || event.key === "k" || event.key === "PageUp") { event.preventDefault(); move(-1); }
    };
    document.addEventListener("keydown", keys);
    stage.addEventListener("wheel", (event) => { if (Math.abs(event.deltaY) > 20) { event.preventDefault(); move(event.deltaY > 0 ? 1 : -1); } }, { passive: false });
    let touchY = null;
    stage.addEventListener("touchstart", (event) => { touchY = event.touches[0].clientY; }, { passive: true });
    stage.addEventListener("touchend", (event) => { if (touchY === null) return; const dy = touchY - event.changedTouches[0].clientY; touchY = null; if (Math.abs(dy) > 50) move(dy > 0 ? 1 : -1); });
    panel.append(stage);
    await show(0);
  }

  // ---------------------------------------------------------------- studio: channel, upload, my videos
  // A moment as the value of a datetime-local input, in the person's own time.
  const localInput = (iso) => {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };
  // For adults: members-only (when people can join, or to untick it on a video already for members) and paid
  // promotion. A reel is always for everyone.
  const extraFields = (info, video) => (adult(info.channel) ? [
    (!video || video.kind !== "reel") && (info.members_plan_on || (video && video.members_only)) ? { name: "members_only", label: "Only for my members", type: "checkbox", hint: "Members pay you monthly to watch it." } : null,
    { name: "paid_promotion", label: "Includes paid promotion", type: "checkbox", hint: "A brand paid you for part of it." },
  ] : []);
  // The latest and earliest a video can be set to go out (the server checks the same).
  const scheduleBounds = () => ({ min: localInput(new Date(Date.now() + 2 * 60000).toISOString()), max: localInput(new Date(Date.now() + 365 * 86400000).toISOString()) });
  const checkSchedule = (value) => {
    const at = new Date(value).getTime();
    if (Number.isNaN(at)) return "Choose the date and time it goes out.";
    if (at < Date.now() - 5 * 60000) return "That time has already passed. Choose a later time, or choose Now.";
    if (at > Date.now() + 365 * 86400000) return "Choose a time within the next year.";
    return "";
  };
  const SHOW = [["", "All"], ["public", "Everyone"], ["unlisted", "Link only"], ["private", "Only me"], ["scheduled", "Scheduled"], ["members", "For members"], ["promoted", "Paid promotion"], ["problems", "Hidden or taken down"]];

  function uploadSection(info) {
    const limits = info.limits;
    const file = h("input", { type: "file", accept: "video/mp4,video/webm,video/quicktime,.mp4,.m4v,.webm,.mov", "aria-label": "Choose a video" });
    const form = S.form([
      { name: "kind", label: "What is it?", type: "select", options: options(KIND) },
      { name: "title", label: "Title", required: true },
      { name: "description", label: "Description", type: "textarea", rows: 3 },
      { name: "category", label: "Category", type: "select", options: options(CATEGORY) },
      { name: "tags", label: "Tags (separate them with commas)" },
      { name: "rating", label: "Who is it suitable for?", type: "select", options: options(RATING), hint: "Children see only videos for everyone; teens never see videos for adults." },
      info.channel.age_group === "child" ? null : { name: "visibility", label: "Who can watch it?", type: "select", options: options(VISIBILITY) },
      { name: "language", label: "Language", type: "select", options: LANGUAGES },
      { name: "for_kids", label: "Made for children", type: "checkbox", hint: "Only family-safe ads run on it." },
      ...extraFields(info),
      info.channel.age_group === "child" ? null : { name: "publish_when", label: "When should it go out?", type: "select", options: [["now", "Now"], ["later", "At a time I choose"]] },
      info.channel.age_group === "child" ? null : { name: "publish_at", label: "Date and time it goes out (your time)", type: "datetime-local", hint: "Until then only you can watch it. Up to a year ahead.", ...scheduleBounds() },
    ].filter(Boolean), { kind: "video", category: "other", rating: "everyone", visibility: "public", language: info.channel.language || "en", publish_when: "now" });
    const laterField = form.inputs.publish_at ? form.inputs.publish_at.control.closest(".field") : null;
    if (laterField) {
      laterField.hidden = true;
      form.inputs.publish_when.control.addEventListener("change", () => {
        laterField.hidden = form.inputs.publish_when.control.value !== "later";
        Object.assign(form.inputs.publish_at.control, scheduleBounds()); // the page may have been open a while
      });
    }
    const filmForm = S.form([
      { name: "genre", label: "Genre", type: "select", options: options(GENRE) },
      { name: "credits", label: "Credits (one per line, like Director: Asha Rao)", type: "textarea", rows: 3 },
    ], { genre: "drama" });
    filmForm.node.hidden = true;
    const membersField = form.inputs.members_only ? form.inputs.members_only.control.closest("label") : null;
    form.inputs.kind.control.addEventListener("change", () => {
      filmForm.node.hidden = form.inputs.kind.control.value !== "short";
      if (membersField) { membersField.hidden = form.inputs.kind.control.value === "reel"; if (membersField.hidden) form.inputs.members_only.control.checked = false; }
    });

    // the picture: a frame from the video, or an image
    const preview = h("video", { muted: true, playsinline: true, preload: "metadata", "aria-label": "Your video" });
    const slider = h("input", { type: "range", min: "0", max: "100", value: "10", step: "0.1", "aria-label": "Choose the moment for the picture" });
    const picked = h("img", { alt: "The picture people will see", hidden: true });
    const pictureFile = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp", "aria-label": "Or choose a picture" });
    const captions = h("input", { type: "file", accept: ".vtt,text/vtt", "aria-label": "Captions (WebVTT, optional)" });
    let frameBlob = null, meta = { duration: 0, width: 0, height: 0 }, objectUrl = "";
    const frameArea = h("div", { class: "vid-frame", hidden: true }, preview, Aira.ui.field("Move to the moment you want as the picture", slider),
      h("div", { class: "btn-row" }, S.button("Use this frame", async () => { frameBlob = await captureFrame(preview); if (frameBlob) { picked.src = URL.createObjectURL(frameBlob); picked.hidden = false; Aira.toast("Picture chosen.", "success"); } }, { kind: "secondary sm" })), picked);
    slider.addEventListener("input", () => { if (meta.duration) preview.currentTime = (Number(slider.value) / 100) * meta.duration; });
    const facts = h("p", { class: "muted", "aria-live": "polite" });
    file.addEventListener("change", async () => {
      const chosen = file.files && file.files[0];
      frameBlob = null; picked.hidden = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (!chosen) { frameArea.hidden = true; return; }
      if (chosen.size > limits.video_bytes) { facts.textContent = `This file is too large: the most is ${size(limits.video_bytes)}.`; return; }
      objectUrl = URL.createObjectURL(chosen);
      preview.src = objectUrl;
      frameArea.hidden = false;
      meta = await readMetadata(preview);
      if (meta.duration) preview.currentTime = Math.min(1, meta.duration / 2);
      const tall = meta.height > meta.width;
      facts.textContent = `${chosen.name} · ${size(chosen.size)}${meta.duration ? ` · ${clockText(meta.duration)}` : ""}${meta.width ? ` · ${meta.width}×${meta.height}${tall ? " (tall)" : ""}` : ""}`;
      if (!form.inputs.title.control.value) form.inputs.title.control.value = chosen.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 100);
      if (tall && meta.duration && meta.duration <= limits.reel_seconds) { form.inputs.kind.control.value = "reel"; form.inputs.kind.control.dispatchEvent(new Event("change")); }
    });

    const bar = h("div", { class: "vid-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0", hidden: true }, h("span", { style: { width: "0%" } }));
    const status = h("p", { class: "muted", "aria-live": "polite" });
    const setProgress = (fraction, text) => { const pct = Math.round(fraction * 100); bar.hidden = false; bar.setAttribute("aria-valuenow", String(pct)); bar.firstChild.style.width = `${pct}%`; status.textContent = text; };
    // Files already sent in this form (the video, the picture, the captions): pressing Upload again after fixing a
    // detail does not send them again. A picture drawn from the video is kept for the same video file.
    const sent = new Map();
    const sendOnce = async (key, blob, kind, onProgress) => {
      if (!sent.has(key)) sent.set(key, await uploadFile(blob, kind, { onProgress }));
      return sent.get(key);
    };
    const fileKey = (item) => `${item.name || "file"}:${item.size}:${item.lastModified || 0}`;
    const send = S.button("Upload", async () => {
      form.setError("");
      const chosen = file.files && file.files[0];
      if (!chosen) { form.setError("Choose a video first."); return; }
      const values = { ...form.read(), ...(form.inputs.kind.control.value === "short" ? filmForm.read() : {}) };
      if (!values.title) { form.setError("Enter a title."); return; }
      if (values.publish_when === "later") {
        if (!values.publish_at) { form.setError("Choose the date and time it goes out."); return; }
        const wrong = checkSchedule(values.publish_at);
        if (wrong) { form.setError(wrong); form.inputs.publish_at.control.focus(); return; }
        values.publish_at = new Date(values.publish_at).toISOString();
      } else delete values.publish_at;
      delete values.publish_when;
      if (values.kind === "reel" && meta.duration > limits.reel_seconds + 0.5) { form.setError(`A reel is at most ${limits.reel_seconds} seconds long.`); return; }
      if (values.kind === "reel" && meta.width && meta.height <= meta.width) { form.setError("A reel is a tall (portrait) video."); return; }
      const details = { ...values, duration: meta.duration, width: meta.width, height: meta.height };
      let uploaded = false;
      try {
        status.textContent = "Checking the details";
        await api("/videos/check", { method: "POST", body: details, silent: true }); // before a large file goes up
        const total = size(chosen.size);
        const videoKey = `video:${fileKey(chosen)}`;
        const video = await sendOnce(videoKey, chosen, "video", (f) => setProgress(f * 0.9, `Uploading ${Math.round(f * 100)}% of ${total}`));
        uploaded = true;
        let thumbId = "";
        const own = pictureFile.files && pictureFile.files[0];
        const picture = own || frameBlob || (meta.width ? await captureFrame(preview, Math.min(1, (meta.duration || 2) / 2)) : null);
        if (picture) {
          setProgress(0.93, "Saving the picture");
          const key = own ? `image:${fileKey(own)}` : frameBlob ? `frame:${videoKey}:${frameBlob.size}` : `auto:${videoKey}`;
          thumbId = (await sendOnce(key, picture instanceof File ? picture : new File([picture], "frame.jpg", { type: "image/jpeg" }), "image")).id;
        }
        let captionsId = "";
        if (captions.files && captions.files[0]) { setProgress(0.96, "Saving the captions"); captionsId = (await sendOnce(`captions:${fileKey(captions.files[0])}`, captions.files[0], "captions")).id; }
        const created = (await api("/videos", { method: "POST", body: { ...details, file_id: video.id, thumb_file_id: thumbId, captions_file_id: captionsId }, silent: true })).video;
        setProgress(1, "Done.");
        Aira.toast(created.scheduled ? `Your video goes out ${outText(created.created_at)}.` : "Your video is up.", "success");
        go({ watch: created.id });
      } catch (error) {
        form.setError(error && error.message ? error.message : String(error));
        status.textContent = uploaded ? "Your video is uploaded. Change what is wrong above and press Upload again: the file is not sent again." : sent.size || bar.hidden ? "Change what is wrong above and press Upload again." : "You can press Upload again: it carries on from where it stopped.";
      }
    }, { kind: "primary" });
    const section = S.section("Upload a video",
      h("p", { class: "muted", text: `MP4 plays everywhere; WebM plays in most browsers. Up to ${size(limits.video_bytes)}. A reel is tall and at most ${limits.reel_seconds} seconds.` }),
      Aira.ui.field("Video file", file), facts, frameArea, Aira.ui.field("Or upload a picture instead", pictureFile), form.node, filmForm.node, Aira.ui.field("Captions (WebVTT, optional)", captions), bar, status, h("div", { class: "btn-row" }, send));
    section.id = "vidUpload";
    // The heading names what is being uploaded (a tall short video turns into a reel by itself).
    const heading = section.querySelector(".suite-h2");
    const UPLOAD_HEADING = { video: "Upload a video", reel: "Upload a reel", short: "Upload a short film" };
    form.inputs.kind.control.addEventListener("change", () => { if (heading) heading.textContent = UPLOAD_HEADING[form.inputs.kind.control.value] || UPLOAD_HEADING.video; });
    // "+ New" (and ?upload=1): this form, set to the kind asked for, with the keyboard on the file chooser.
    hooks.upload = (kind) => {
      if (KIND[kind] && form.inputs.kind.control.value !== kind) { form.inputs.kind.control.value = kind; form.inputs.kind.control.dispatchEvent(new Event("change")); }
      section.scrollIntoView({ block: "start" });
      file.focus({ preventScroll: true });
    };
    return section;
  }

  function editVideo(video, info) {
    const child = info.channel.age_group === "child";
    return S.edit({
      title: "Edit video",
      fields: [
        { name: "title", label: "Title", required: true },
        { name: "description", label: "Description", type: "textarea", rows: 4, hint: "Lines like 0:00 Intro and 1:30 The recipe become chapters (the first at 0:00, at least two)." },
        { name: "category", label: "Category", type: "select", options: options(CATEGORY) },
        { name: "tags", label: "Tags (separate them with commas)" },
        { name: "rating", label: "Who is it suitable for?", type: "select", options: options(RATING) },
        child ? null : { name: "visibility", label: "Who can watch it?", type: "select", options: options(VISIBILITY) },
        { name: "language", label: "Language", type: "select", options: LANGUAGES },
        video.kind === "short" ? { name: "genre", label: "Genre", type: "select", options: options(GENRE) } : null,
        video.kind === "short" ? { name: "credits", label: "Credits (one per line, like Director: Asha Rao)", type: "textarea", rows: 3 } : null,
        { name: "for_kids", label: "Made for children", type: "checkbox" },
        adult(info.channel) ? { name: "ads_enabled", label: "Allow ads on this video (when your channel earns)", type: "checkbox" } : null,
        ...extraFields(info, video),
        video.scheduled ? { name: "publish_at", label: "Goes out on (your time)", type: "datetime-local", hint: "Empty it to send the video out now.", ...scheduleBounds() } : null,
      ].filter(Boolean),
      values: { ...video, tags: (video.tags || []).join(", "), credits: (video.credits || []).map((item) => (item.role ? `${item.role}: ${item.name}` : item.name)).join("\n"), publish_at: video.scheduled ? localInput(video.created_at) : "" },
      onSave: async (values) => {
        if (video.scheduled && values.publish_at) { const wrong = checkSchedule(values.publish_at); if (wrong) throw new Error(wrong); }
        if (video.scheduled) values.publish_at = values.publish_at ? new Date(values.publish_at).toISOString() : "";
        else delete values.publish_at;
        await api(`/videos/${video.id}`, { method: "PATCH", body: values });
        Aira.toast("Saved.", "success");
        Aira.router.render();
      },
    });
  }

  async function studioTab(panel, info, route) {
    const q = String(route.query.q || "").slice(0, 100);
    const show = SHOW.some(([id]) => id === route.query.show) ? route.query.show : "";
    const filter = `${q ? `q=${encodeURIComponent(q)}&` : ""}${show ? `show=${show}` : ""}`;
    const [studio, notes] = await Promise.all([api(`/studio?${filter}`), api("/notifications")]);
    const channel = info.channel;
    const channelForm = S.form([
      { name: "name", label: "Channel name", required: true },
      { name: "avatar", label: "Picture (an emoji)" },
      { name: "about", label: "About your channel", type: "textarea", rows: 2 },
      { name: "country", label: "Your country (two letters, like IN)", hint: "Ads can be chosen for viewers in a country." },
      { name: "language", label: "Your language", type: "select", options: LANGUAGES },
      { name: "restricted", label: "Restricted mode: hide videos for adults", type: "checkbox" },
    ], channel);
    panel.append(S.grid(
      S.section("Your channel", h("p", { class: "muted", text: `${subscribersText(studio.subscribers)} · ${studio.total === 1 ? "1 video" : `${studio.total} videos`}` }), channelForm.node,
        h("div", { class: "btn-row" }, S.button("Save channel", async () => { try { await api("/me", { method: "PATCH", body: channelForm.read() }); Aira.toast("Saved.", "success"); } catch (error) { channelForm.setError(error.message); } }, { kind: "secondary" }), h("a", { class: "btn ghost", href: `#/videos?channel=${channel.id}`, text: "See your channel" }))),
      uploadSection(info)));
    // Find a video among your own: words, then who can watch it and what kind; the list as a spreadsheet.
    const findInput = h("input", { type: "search", value: q, placeholder: "Search your videos", "aria-label": "Search your videos", maxlength: "100" });
    const finder = h("form", { class: "vid-row", role: "search", onsubmit: (event) => { event.preventDefault(); go({ tab: "studio", ...(findInput.value.trim() ? { q: findInput.value.trim() } : {}), ...(show ? { show } : {}) }); } },
      h("div", { style: { flex: "1 1 200px" } }, findInput), h("button", { type: "submit", class: "btn secondary sm", text: "Find" }),
      S.button("Download CSV", () => Aira.download(`/api/videos/studio?format=csv&${filter}`, "your-videos.csv"), { kind: "ghost sm" }));
    const showChips = h("div", { class: "vid-chips", role: "group", "aria-label": "Show" }, ...SHOW.map(([id, label]) => h("button", { type: "button", class: `btn sm ${show === id ? "secondary" : "ghost"}`, "aria-pressed": String(show === id), text: label, onclick: (event) => goFrom(event, { tab: "studio", ...(q ? { q } : {}), ...(id ? { show: id } : {}) }) })));
    const filtered = Boolean(q || show);
    panel.append(S.section(filtered ? `Your videos (${studio.videos.length} of ${studio.total})` : `Your videos (${studio.total})`, studio.total ? finder : null, studio.total ? showChips : null, studio.videos.length ? h("ul", { class: "vid-mine" }, ...studio.videos.map((row) => h("li", { class: "vid-mine-item" },
      h("a", { class: "vid-mine-thumb", href: `#/videos?watch=${row.id}`, "aria-label": `Watch ${row.title}` }, thumb(row)),
      h("div", { class: "vid-mine-body" },
        h("a", { class: "vid-mine-title", href: `#/videos?watch=${row.id}`, text: row.title }),
        h("span", { class: "vid-meta", text: `${KIND[row.kind]} · ${VISIBILITY[row.visibility]} · ${viewsText(row.views)} · ${likesText(row.likes)} · ${row.scheduled ? `goes out ${outText(row.created_at)}` : lower(when(row.created_at))}` }),
        row.blocked || row.scheduled || row.members_only || row.paid_promotion ? h("div", { class: "vid-chips" },
          row.blocked ? h("span", { class: "suite-pill bad", text: BLOCKED[row.blocked] || row.blocked }) : null,
          row.scheduled ? h("span", { class: "suite-pill warn", text: "Scheduled" }) : null,
          row.members_only ? h("span", { class: "suite-pill info", text: "For members" }) : null,
          row.paid_promotion ? h("span", { class: "suite-pill", text: "Paid promotion" }) : null) : null,
        h("div", { class: "btn-row" },
          S.button("Edit", async () => editVideo((await api(`/videos/${row.id}`)).video, info), { kind: "ghost sm" }),
          S.button("Share", async () => share({ ...(await api(`/videos/${row.id}`)).video }), { kind: "ghost sm" }),
          S.button("Delete", async () => { if (await S.confirmDelete(`“${row.title}”`)) { await api(`/videos/${row.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" })))))) : filtered ? Aira.ui.empty("No videos match", "Try other words, or press All.") : Aira.ui.empty("No videos yet", "Upload your first video above.")));
    if (studio.notices.length || studio.strikes) {
      panel.append(S.section(`Copyright (${studio.strikes === 1 ? "1 strike" : `${studio.strikes} strikes`})`,
        h("p", { class: "muted", text: "A video taken down after a copyright notice is a strike for 90 days. With three strikes a channel cannot upload. If the video is yours, or you may use what it shows, send a counter-notice." }),
        h("ul", { class: "life-lines" }, ...studio.notices.map((item) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
          h("div", { style: { flex: "1 1 240px", minWidth: "0" } }, h("strong", { text: item.video.title }), h("span", { class: "life-line-sub", text: `${NOTICE[item.status] || item.status} · claimed by ${item.claimant_name}${item.claimant_organization ? ` for ${item.claimant_organization}` : ""}` }), h("span", { class: "life-line-sub muted", text: item.work })),
          item.status === "taken_down" && adult(channel) ? S.button("Send a counter-notice", () => S.edit({
            title: "Counter-notice",
            fields: [
              { name: "name", label: "Your full name", required: true },
              { name: "statement", label: "Why should the video come back? (your own work, licensed, or fair use)", type: "textarea", rows: 4, required: true },
              { name: "sworn", label: "I believe in good faith the video was removed by mistake, and the claimant may see my name and statement.", type: "checkbox" },
            ],
            submitLabel: "Send counter-notice",
            onSave: async (values) => { await api(`/notices/${item.id}/counter`, { method: "POST", body: values }); Aira.toast("Your counter-notice was sent.", "success"); Aira.router.render(); },
          }), { kind: "secondary sm" }) : null)))));
    }
    panel.append(S.section(notes.unread ? `Notifications (${notes.unread} new)` : "Notifications",
      notes.notifications.length ? h("ul", { class: "life-lines" }, ...notes.notifications.map((item) => h("li", { class: "life-line" }, h("span", { class: "life-line-text", text: item.text }), h("span", { class: "life-line-sub muted", text: when(item.created_at) })))) : h("p", { class: "muted", text: "Nothing yet. New subscribers, comments and messages from the moderators appear here." }),
      notes.unread ? h("div", { class: "btn-row" }, S.button("Mark all as read", async () => { await api("/notifications/read", { method: "POST", body: {} }); Aira.router.render(); }, { kind: "ghost sm" })) : null));
  }

  // ---------------------------------------------------------------- earnings: every way a creator earns
  const EARN_PARTS = [["overview", "Overview"], ["ads", "Ads and payouts"], ["memberships", "Memberships"], ["deals", "Brand deals"], ["support", "Support from viewers"]];
  const moneyList = (items) => (items && items.length ? items.map((item) => item.amount).join(" + ") : "");
  const monthName = (month) => { const date = new Date(`${month}-01T12:00:00`); return Number.isNaN(date.getTime()) ? month : date.toLocaleDateString([], { month: "short", year: "numeric" }); };
  // From a button elsewhere on the page the keyboard lands on the part's own button.
  const toPart = (part) => { refocus = ["Earnings", (EARN_PARTS.find(([id]) => id === part) || ["", ""])[1]]; go({ tab: "earnings", part }); };

  async function earningsTab(panel, info, route) {
    const part = EARN_PARTS.some(([id]) => id === route.query.part) ? route.query.part : "overview";
    panel.append(h("nav", { class: "vid-chips", "aria-label": "Earnings" }, ...EARN_PARTS.map(([id, label]) => h("button", { type: "button", class: `btn sm ${part === id ? "secondary" : "ghost"}`, "aria-pressed": String(part === id), text: label, onclick: () => toPart(id) }))));
    if (part === "ads") await adsPart(panel, info);
    else if (part === "memberships") await membershipsPart(panel, info);
    else if (part === "deals") await dealsPart(panel, info, route);
    else if (part === "support") panel.append(supportSettings(await api("/support"), info));
    else await overviewPart(panel, route);
  }

  // All income by month, where it comes from, this month's outlook and the log of every payment.
  // A table that becomes one card per row on a narrow screen: each cell says which column it is (app-videos.css).
  function stacked(node) {
    const table = node.querySelector ? node.querySelector("table") : null;
    if (!table) return node;
    const labels = [...table.querySelectorAll("thead th")].map((th) => th.textContent);
    for (const tr of table.querySelectorAll("tbody tr")) [...tr.children].forEach((td, index) => td.setAttribute("data-label", labels[index] || ""));
    node.classList.add("vid-stack");
    return node;
  }

  async function overviewPart(panel, route) {
    const data = await api("/income");
    const o = data.outlook, ways = data.ways, now = data.months[0];
    panel.append(S.kpis([
      { label: "This month so far", value: moneyList(now.total) || usd(0), hint: "from every way you earn" },
      { label: "Ads this month, on track for", value: usd(o.ads_projected_micros), hint: o.ads_so_far_micros ? `${usd(o.ads_so_far_micros)} in ${o.days_elapsed} of ${o.days_in_month} days, at the same pace (a guess, not a promise)` : "no ad earnings yet this month" },
      { label: "Members", value: String(o.members_active), hint: o.members_active ? `${o.members_monthly.amount} a month${o.members_renewing ? ` · ${o.members_renewing} to renew this month` : ""}` : "no members yet" },
      { label: "Brand deals to be paid", value: moneyList(o.deals_due) || usd(0), hint: "agreed or delivered, due by the end of the month" },
    ]));
    const way = (done, label, hint, part, action) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
      h("span", { class: `suite-pill ${done ? "good" : ""}`, text: done ? "On" : "Off" }),
      h("div", { style: { flex: "1 1 220px", minWidth: "0" } }, h("span", { class: "life-line-text", text: label }), h("span", { class: "life-line-sub muted", text: hint })),
      S.button(done ? "Open" : action, () => toPart(part), { kind: done ? "ghost sm" : "secondary sm" }));
    panel.append(S.section("Ways to earn",
      ways.terms ? null : h("p", { class: "muted", text: "Start by accepting the creator terms under Ads and payouts: then every way below opens up." }),
      h("ul", { class: "life-lines" },
        way(ways.ads, "Ads on your videos", "Businesses pay to show ads on your videos; you get a share.", "ads", "Turn on"),
        way(ways.memberships, "Memberships", "Fans pay you every month to watch your videos for members.", "memberships", "Set up"),
        way(ways.deals, "Brand deals", "Brands pay you to mention them; keep each deal from first message to payment.", "deals", "Add a deal"),
        way(ways.support, "Support from viewers", "Viewers send you what they like, on your own payment page or by UPI.", "support", "Turn on"))));
    const labels = data.labels;
    const highlights = [
      data.best_month ? h("div", { class: "card vid-highlight" }, h("span", { class: "vid-meta", text: "Best month" }), h("strong", { text: monthName(data.best_month) }), h("span", { class: "vid-meta", text: moneyList(data.months.find((item) => item.month === data.best_month).total) })) : null,
      data.lowest_month ? h("div", { class: "card vid-highlight" }, h("span", { class: "vid-meta", text: "Lowest month" }), h("strong", { text: monthName(data.lowest_month) }), h("span", { class: "vid-meta", text: moneyList(data.months.find((item) => item.month === data.lowest_month).total) })) : null,
      data.top_source ? h("div", { class: "card vid-highlight" }, h("span", { class: "vid-meta", text: "Most of your money comes from" }), h("strong", { text: labels[data.top_source] }), h("span", { class: "vid-meta", text: moneyList(data.by_source[data.top_source]) })) : null,
    ].filter(Boolean);
    if (highlights.length) panel.append(h("div", { class: "vid-highlights" }, ...highlights));
    const months = data.months.slice().reverse();
    const oneCurrency = data.total.length === 1;
    panel.append(S.section("The last 12 months",
      oneCurrency ? S.bars(months.map((item) => ({ label: monthName(item.month).split(" ")[0], value: item.total_cents })), { format: (cents) => S.money(cents, data.total[0].currency) }) : null,
      stacked(S.table({
        caption: "Income by month",
        columns: [
          { key: "month", label: "Month", render: (row) => h("span", {}, h("span", { text: monthName(row.month) }), row.month === data.best_month ? h("span", { class: "suite-pill good", text: "best" }) : null, row.month === data.lowest_month ? h("span", { class: "suite-pill warn", text: "lowest" }) : null) },
          { key: "ads", label: "Ads", num: true, render: (row) => moneyList(row.ads) || "–" },
          { key: "memberships", label: "Members", num: true, render: (row) => moneyList(row.memberships) || "–" },
          { key: "deals", label: "Brand deals", num: true, render: (row) => moneyList(row.deals) || "–" },
          { key: "support", label: "Support", num: true, render: (row) => moneyList(row.support) || "–" },
          { key: "total", label: "Total", num: true, render: (row) => h("strong", { text: moneyList(row.total) || "–" }) },
        ],
        rows: data.months.filter((item) => item.total_cents),
        empty: Aira.ui.empty("No income yet", "Turn on a way to earn above; what you earn shows here month by month."),
      })),
      h("p", { class: "vid-meta", text: "Each currency is shown on its own; nothing is converted. Ads are estimated until the month is closed." })));
    // The log of every payment, newest first, with a filter by where it came from.
    const source = Object.prototype.hasOwnProperty.call(labels, route.query.source || "") ? route.query.source : "";
    const rows = data.log.filter((item) => !source || item.source === source);
    panel.append(S.section("Income log",
      h("div", { class: "vid-row", style: { justifyContent: "space-between" } },
        h("div", { class: "vid-chips", role: "group", "aria-label": "From" }, ...[["", "Everything"], ...Object.entries(labels)].map(([id, label]) => h("button", { type: "button", class: `btn sm ${source === id ? "secondary" : "ghost"}`, "aria-pressed": String(source === id), text: label, onclick: (event) => goFrom(event, { tab: "earnings", part: "overview", ...(id ? { source: id } : {}) }) }))),
        S.button("Download CSV", () => Aira.download("/api/videos/income?format=csv", "videos-income.csv"), { kind: "secondary sm" })),
      S.table({
        caption: "Every payment in the last 12 months",
        columns: [
          { key: "at", label: "Date", render: (row) => S.day(row.at) },
          { key: "label", label: "From", render: (row) => h("span", {}, h("strong", { text: row.from }), h("span", { class: "vid-meta", text: ` · ${row.label}` })) },
          { key: "amount", label: "Amount", num: true, render: (row) => `${row.amount}${row.estimated ? " (estimated)" : ""}` },
        ],
        rows,
        empty: Aira.ui.empty(source ? "Nothing from here yet" : "No payments yet", "Money you receive appears here, newest first."),
      })));
  }

  // Memberships: the plan, payments to confirm, and the members.
  async function membershipsPart(panel, info) {
    if (!adult(info.channel)) { panel.append(Aira.ui.empty("Memberships are for adults", "")); return; }
    const data = await api("/memberships");
    const plan = data.plan;
    panel.append(S.kpis([
      { label: "Members now", value: String(plan.active_members) },
      { label: "A month from members", value: moneyList(plan.monthly) || S.money(0, plan.currency), hint: "by what each member paid" },
      { label: "Videos for members", value: String(plan.members_only_videos), hint: "tick Only for my members when you upload or edit" },
      { label: "Received so far", value: moneyList(data.totals) || S.money(0, plan.currency) },
    ]));
    const form = S.form([
      { name: "enabled", label: "Let people join my channel", type: "checkbox", hint: "A Join button appears on your channel and on your videos for members." },
      { name: "price_cents", label: "Monthly price", type: "money", required: true },
      { name: "currency", label: "Currency", type: "select", options: plan.currencies.map((code) => [code, code]) },
      { name: "perks", label: "What members get (for example: a new video for members every week)", type: "textarea", rows: 3 },
    ], plan);
    const save = S.button("Save", async () => {
      form.setError("");
      try { await api("/memberships", { method: "PUT", body: form.read() }); Aira.toast("Saved.", "success"); Aira.router.render(); } catch (error) { form.setError(error.message); }
    }, { kind: "secondary" });
    const blockers = plan.why_not.length && !plan.enabled ? h("div", { class: "suite-body" },
      h("ul", { class: "life-lines" }, ...plan.why_not.map((reason) => h("li", { class: "life-line", text: reason }))),
      h("div", { class: "btn-row" }, S.button("Ads and payouts", () => toPart("ads"), { kind: "ghost sm" }), S.button("Support from viewers", () => toPart("support"), { kind: "ghost sm" }))) : null;
    panel.append(S.section("Your membership",
      h("p", { class: "muted", text: "Members pay you directly every month, on your own payment page or by UPI: Monosage takes nothing. Each payment has a reference; mark it received when the money arrives and the member can watch your videos for members for that many months." }),
      blockers, form.node, h("div", { class: "btn-row" }, save)));
    const decide = async (payment, action) => {
      if (action === "not_received" && payment.status === "received" && !(await Aira.confirm({ title: "Mark this payment not received?", message: `${payment.member.name} loses the ${monthsText(payment.months)} it paid for and cannot watch your videos for members for that time. Do this only if the money never arrived.`, confirmLabel: "Mark not received", danger: true }))) return;
      await api(`/memberships/${payment.id}`, { method: "POST", body: { action } });
      Aira.toast(action === "received" ? "Marked received. Welcome your new member!" : "Marked not received. If that was a mistake, press Received on it.", "success");
      Aira.router.render();
    };
    panel.append(S.section(data.waiting ? `Payments (${data.waiting} waiting)` : "Payments",
      data.payments.length ? h("ul", { class: "life-lines" }, ...data.payments.map((payment) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
        h("div", { style: { flex: "1 1 240px", minWidth: "0" } },
          h("span", { class: "life-line-text", text: `${payment.amount} from ${payment.member.name} for ${monthsText(payment.months)}` }),
          h("span", { class: "life-line-sub muted", text: `${TIP_STATUS[payment.status] || payment.status} · reference ${payment.reference} · ${lower(when(payment.created_at))}` })),
        payment.status === "pledged" || payment.status === "declined" ? S.button("Received", () => decide(payment, "received"), { kind: payment.status === "pledged" ? "sm" : "ghost sm" }) : null,
        payment.status === "pledged" || payment.status === "received" ? S.button("Not received", () => decide(payment, "not_received"), { kind: "ghost sm" }) : null))) : Aira.ui.empty("No payments yet", plan.enabled ? "When someone joins, their payment shows here. Mark it received when the money arrives." : "Turn on memberships above so people can join.")));
    panel.append(S.section("Members", S.table({
      caption: "Your members",
      columns: [
        { key: "member", label: "Member", render: (row) => `${row.member.avatar} ${row.member.name}` },
        { key: "paid_until", label: "Paid until", render: (row) => S.day(row.paid_until) },
        { key: "active", label: "Status", render: (row) => S.pill(row.active ? "active" : "ended", { ended: "" }) },
      ],
      rows: data.members,
      empty: Aira.ui.empty("No members yet", "People who join and pay appear here."),
    })));
  }

  // Brand deals: the creator's sponsorships from the first message to the money received.
  const DEAL_STATUS = { proposed: "Proposed", agreed: "Agreed", delivered: "Delivered", paid: "Paid", cancelled: "Cancelled" };
  const DEAL_TONES = { proposed: "", agreed: "info", delivered: "warn", paid: "good", cancelled: "bad" };

  async function dealForm(deal, data) {
    const { videos } = await api("/studio");
    return S.edit({
      title: deal ? `Edit the deal with ${deal.brand}` : "New brand deal",
      fields: [
        { name: "brand", label: "Brand", required: true },
        { name: "contact", label: "Who to talk to (name, email or phone)" },
        { name: "amount_cents", label: "Amount", type: "money" },
        { name: "currency", label: "Currency", type: "select", options: Object.keys(CURRENCY_NAMES).map((code) => [code, code]) },
        { name: "status", label: "Where is it?", type: "select", options: data.statuses.map((id) => [id, DEAL_STATUS[id] || id]) },
        { name: "due_on", label: "Due on", type: "date" },
        { name: "paid_on", label: "Paid on (for a paid deal)", type: "date", max: localInput(new Date().toISOString()).slice(0, 10), hint: "The day the money arrived. Empty means today." },
        { name: "video_id", label: "The video it is for", type: "select", options: [["", "No video yet"], ...videos.map((video) => [video.id, video.title])], hint: "That video tells viewers it includes paid promotion." },
        { name: "notes", label: "Notes (what you agreed)", type: "textarea", rows: 3 },
      ],
      values: deal ? { ...deal, paid_on: deal.paid_on || "" } : { status: "proposed", currency: "USD" },
      submitLabel: deal ? "Save" : "Add deal",
      onSave: async (values) => {
        if (values.status !== "paid") delete values.paid_on;
        if (deal) await api(`/deals/${deal.id}`, { method: "PATCH", body: values });
        else await api("/deals", { method: "POST", body: values });
        Aira.toast(values.status === "paid" ? "Saved. The money is in your income." : "Saved.", "success");
        Aira.router.render();
      },
    });
  }
  const CURRENCY_NAMES = { USD: 1, EUR: 1, GBP: 1, INR: 1, CAD: 1, AUD: 1, SGD: 1, AED: 1, JPY: 1 };

  async function dealsPart(panel, info, route) {
    if (!adult(info.channel)) { panel.append(Aira.ui.empty("Brand deals are for adults", "")); return; }
    const statuses = ["open", ...Object.keys(DEAL_STATUS)];
    const status = statuses.includes(route.query.status) ? route.query.status : "";
    const q = String(route.query.q || "").slice(0, 100);
    const query = `${status ? `status=${status}&` : ""}${q ? `q=${encodeURIComponent(q)}` : ""}`;
    const data = await api(`/deals?${query}`);
    hooks.deal = () => dealForm(null, data); // "+ New brand deal"
    panel.append(S.kpis([
      { label: "Waiting to be paid", value: moneyList(data.waiting) || usd(0), hint: "agreed or delivered" },
      { label: "Paid this year", value: moneyList(data.paid_this_year) || usd(0) },
      { label: "Late", value: String(data.overdue), tone: data.overdue ? "warn" : "", hint: "past their due date and not paid" },
    ]));
    const search = h("input", { type: "search", value: q, placeholder: "Search brands, people and notes", "aria-label": "Search your deals", maxlength: "100" });
    const there = (next, event) => (event ? goFrom(event, { tab: "earnings", part: "deals", ...next }) : go({ tab: "earnings", part: "deals", ...next }));
    panel.append(S.section("Brand deals",
      h("div", { class: "btn-row" }, S.button("New deal", () => dealForm(null, data), { kind: "primary" }), S.button("Download CSV", () => Aira.download(`/api/videos/deals?format=csv&${query}`, "brand-deals.csv"), { kind: "ghost sm" })),
      h("form", { class: "vid-row", role: "search", onsubmit: (event) => { event.preventDefault(); there({ ...(status ? { status } : {}), ...(search.value.trim() ? { q: search.value.trim() } : {}) }); } }, h("div", { style: { flex: "1 1 200px" } }, search), h("button", { type: "submit", class: "btn secondary sm", text: "Find" })),
      h("div", { class: "vid-chips", role: "group", "aria-label": "Show" }, ...[["", "All"], ["open", "Not paid yet"], ...Object.entries(DEAL_STATUS)].map(([id, label]) => h("button", { type: "button", class: `btn sm ${status === id ? "secondary" : "ghost"}`, "aria-pressed": String(status === id), text: label, onclick: (event) => there({ ...(id ? { status: id } : {}), ...(q ? { q } : {}) }, event) }))),
      data.deals.length ? h("ul", { class: "vid-deals" }, ...data.deals.map((deal) => h("li", { class: "vid-deal" },
        h("div", { class: "vid-deal-main" },
          h("strong", { class: "vid-mine-title", text: deal.brand }),
          h("span", { class: "vid-meta", text: [deal.amount, deal.contact, deal.due_on ? `due ${S.day(deal.due_on)}` : "", deal.paid_at ? `paid ${S.day(deal.paid_at)}` : ""].filter(Boolean).join(" · ") }),
          deal.video ? h("a", { class: "vid-meta", href: `#/videos?watch=${deal.video.id}`, text: `For: ${deal.video.title}` }) : null,
          deal.notes ? h("span", { class: "vid-meta", style: { whiteSpace: "pre-wrap" }, text: deal.notes }) : null),
        h("div", { class: "vid-chips" }, S.pill(deal.status, DEAL_TONES), deal.overdue ? h("span", { class: "suite-pill bad", text: "Late" }) : null),
        h("div", { class: "btn-row" },
          deal.status !== "paid" && deal.status !== "cancelled" ? S.button("Mark paid", async () => { await api(`/deals/${deal.id}`, { method: "PATCH", body: { status: "paid" } }); Aira.toast("Marked paid. The money is in your income.", "success"); Aira.router.render(); }, { kind: "secondary sm" }) : null,
          S.button("Edit", () => dealForm(deal, data), { kind: "ghost sm" }),
          S.button("Delete", async () => { if (await S.confirmDelete(`the deal with ${deal.brand}`)) { await api(`/deals/${deal.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }))))) : (status || q ? Aira.ui.empty("No deals match", "Try other words, or press All.") : Aira.ui.empty("No brand deals yet", "When a brand asks you to mention them, add the deal here and follow it until you are paid."))));
  }

  // Ads and payouts: the creator's side of ads.
  async function adsPart(panel, info) {
    const data = await api("/earnings");
    const b = data.balances, rules = data.settings, state = data.eligibility;
    panel.append(S.kpis([
      { label: "Earned today", value: usd(data.today_micros), hint: "estimated" },
      { label: "This month", value: usd(data.month_micros), hint: "estimated until the month closes" },
      { label: "Ready to be paid", value: usd(Math.max(0, b.payable_micros)), tone: b.payable_micros >= rules.min_payout_micros ? "good" : "" },
      { label: "Paid to you", value: usd(b.paid_micros) },
    ]));
    const steps = state.checks.map((check) => ({ label: check.label, done: check.done }));
    const actions = [];
    if (!data.terms_accepted_at) actions.push(S.button("I accept the creator terms", async () => { await api("/earnings/terms", { method: "POST", body: {} }); Aira.router.render(); }, { kind: "primary" }));
    if (state.eligible || state.monetized) actions.push(S.button(state.monetized ? "Turn off ads on my videos" : "Turn on ads on my videos", async () => { await api("/earnings/monetize", { method: "POST", body: { on: !state.monetized } }); Aira.toast(state.monetized ? "Ads are off on your videos." : "Ads can now run on your videos.", "success"); Aira.router.render(); }, { kind: state.monetized ? "ghost" : "primary" }));
    panel.append(S.grid(
      S.section(state.monetized ? "You are earning" : "Start earning", S.checklist(steps), actions.length ? h("div", { class: "btn-row" }, ...actions) : null),
      S.section("How the money works",
        h("p", { text: `Businesses pay for the ads shown on your videos. You get ${rules.revenue_share_percent}% of what they pay; Monosage keeps the rest.` }),
        h("p", { text: "An ad counts when it plays or is on screen for 2 seconds. Your own views, your household's views and repeated views do not count." }),
        h("p", { text: `Earnings are estimated until the month is closed and checked for invalid traffic. Then they are confirmed, and you can ask to be paid once you have at least ${usd(rules.min_payout_micros)}.` }),
        !data.terms_accepted_at ? h("p", { class: "muted", text: "Creator terms: you own or may use everything in your videos; you do not buy, swap or fake views or clicks; earnings from invalid traffic are taken back; you give correct payment details; Monosage can stop ads on a channel that breaks these rules." }) : null)));
    panel.append(S.section("This month by video", S.table({
      columns: [
        { key: "title", label: "Video", render: (row) => h("a", { href: `#/videos?watch=${row.video_id}`, text: row.title }) },
        { key: "views", label: "Views", num: true },
        { key: "impressions", label: "Ads shown", num: true },
        { key: "clicks", label: "Ad clicks", num: true },
        { key: "earned_micros", label: "Earned", num: true, render: (row) => usd(row.earned_micros) },
        { key: "rpm_micros", label: "Per 1,000 views", num: true, render: (row) => usd(row.rpm_micros) },
      ],
      rows: data.videos,
      empty: Aira.ui.empty("No ad earnings this month yet", state.monetized ? "When ads run on your videos, what you earn shows here." : "Turn on ads on your videos to start earning."),
    })));
    panel.append(S.section("Months", S.table({
      columns: [
        { key: "month", label: "Month" },
        { key: "earned_micros", label: "Earned", num: true, render: (row) => usd(row.earned_micros) },
        { key: "clawed_micros", label: "Taken back", num: true, render: (row) => (row.clawed_micros ? usd(row.clawed_micros) : "") },
        { key: "confirmed", label: "Status", render: (row) => S.pill(row.confirmed ? "confirmed" : "pending") },
      ],
      rows: data.months,
      empty: Aira.ui.empty("Nothing yet", "Each month of earnings appears here."),
    })));
    const canAsk = b.payable_micros >= rules.min_payout_micros && !data.payouts.some((item) => item.status === "requested");
    panel.append(S.section("Payouts",
      h("p", { class: "muted", text: canAsk ? `You can ask for up to ${usd(b.payable_micros)}.` : `You can ask to be paid when at least ${usd(rules.min_payout_micros)} is confirmed.` }),
      canAsk ? h("div", { class: "btn-row" }, S.button("Ask to be paid", () => S.edit({
        title: "Ask to be paid",
        fields: [
          { name: "amount_cents", label: "Amount (USD)", type: "money", required: true },
          { name: "details", label: "Where should the money go? (bank account or UPI id)", type: "textarea", rows: 3, required: true },
        ],
        values: { amount_cents: Math.floor(b.payable_micros / 10000), details: data.payout_details },
        submitLabel: "Send request",
        onSave: async (values) => { await api("/earnings/payouts", { method: "POST", body: values }); Aira.toast("Your request was sent. You are told when it is paid.", "success"); Aira.router.render(); },
      }), { kind: "primary" })) : null,
      S.table({
        columns: [
          { key: "requested_at", label: "Asked", render: (row) => S.day(row.requested_at) },
          { key: "amount_micros", label: "Amount", num: true, render: (row) => usd(row.amount_micros) },
          { key: "status", label: "Status", render: (row) => S.pill(row.status === "requested" ? "pending" : row.status === "rejected" ? "declined" : row.status) },
          { key: "reference", label: "Reference", render: (row) => row.reference || row.note || "" },
        ],
        rows: data.payouts,
        empty: Aira.ui.empty("No payouts yet", "Payments made to you appear here with their reference."),
      })));
  }

  // The creator's side of support: where viewers pay, and the pledges to mark received.
  function supportSettings(support, info) {
    const settings = support.settings;
    const form = S.form([
      { name: "enabled", label: "Let viewers support my channel", type: "checkbox", hint: "A Support button appears under your videos and on your channel." },
      { name: "link", label: "Your payment page (https://, for example your PayPal.me or Razorpay page)" },
      { name: "upi", label: "Your UPI id (like name@bank)" },
      { name: "currency", label: "Currency", type: "select", options: settings.currencies.map((code) => [code, code]) },
      { name: "note", label: "A note for your supporters", type: "textarea", rows: 2 },
    ], settings);
    const save = S.button("Save", async () => {
      form.setError("");
      try { await api("/support", { method: "PUT", body: form.read() }); Aira.toast("Saved.", "success"); Aira.router.render(); } catch (error) { form.setError(error.message); }
    }, { kind: "secondary" });
    const decide = async (tip, action) => {
      if (action === "not_received" && tip.status === "received" && !(await Aira.confirm({ title: "Mark this support not received?", message: `It stops counting in your income. Do this only if the money from ${tip.supporter.name} never arrived.`, confirmLabel: "Mark not received", danger: true }))) return;
      await api(`/support/${tip.id}`, { method: "POST", body: { action } });
      Aira.toast(action === "received" ? "Marked received. Your supporter is thanked." : "Marked not received. If that was a mistake, press Received on it.", "success");
      Aira.router.render();
    };
    const list = support.tips.length ? h("ul", { class: "life-lines" }, ...support.tips.map((tip) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
      h("div", { style: { flex: "1 1 240px", minWidth: "0" } },
        h("span", { class: "life-line-text", text: `${tip.amount} from ${tip.supporter.name}` }),
        h("span", { class: "life-line-sub", text: `${TIP_STATUS[tip.status] || tip.status} · reference ${tip.reference} · ${lower(when(tip.created_at))}${tip.video ? ` · on ${tip.video.title}` : ""}` }),
        tip.message ? h("span", { class: "life-line-sub muted", text: `“${tip.message}”` }) : null),
      tip.status === "pledged" || tip.status === "declined" ? S.button("Received", () => decide(tip, "received"), { kind: tip.status === "pledged" ? "sm" : "ghost sm" }) : null,
      tip.status === "pledged" || tip.status === "received" ? S.button("Not received", () => decide(tip, "not_received"), { kind: "ghost sm" }) : null))) : Aira.ui.empty("No support yet", settings.enabled ? "When a viewer pledges, it shows here. Mark it received when the money arrives." : "Turn on support so viewers can pay you directly.");
    return S.section(support.waiting ? `Support from viewers (${support.waiting} waiting)` : "Support from viewers",
      h("p", { class: "muted", text: "Viewers pay you directly, on your own payment page or by UPI: Monosage takes nothing and never holds the money. Each supporter gets a reference to put in the payment note; mark the pledge received when the money arrives." }),
      support.totals.length ? h("p", { text: `Received so far: ${support.totals.map((item) => item.amount).join(" + ")}` }) : null,
      settings.can_enable || settings.enabled ? h("div", { class: "suite-body" }, form.node, h("div", { class: "btn-row" }, save)) : adult(info.channel) ? h("div", { class: "btn-row" }, h("span", { class: "muted", text: "Accept the creator terms first." }), S.button("Ads and payouts", () => toPart("ads"), { kind: "ghost sm" })) : h("p", { class: "muted", text: "Earning money is for adults." }),
      list);
  }

  // ---------------------------------------------------------------- analytics (the creator's numbers)
  const watchTime = (seconds) => {
    const value = Number(seconds || 0);
    if (value >= 3600) return `${(value / 3600).toFixed(1)} hours`;
    if (value >= 60) return `${Math.round(value / 60)} minutes`;
    return `${Math.round(value)} seconds`;
  };
  const change = (now, before) => {
    if (!before) return now ? "new this period" : "";
    const delta = Math.round((100 * (now - before)) / before);
    return delta === 0 ? "same as the period before" : `${delta > 0 ? "+" : ""}${delta}% on the period before`;
  };

  function dailyChart(daily, key, label, format) {
    const top = Math.max(1, ...daily.map((item) => Number(item[key]) || 0));
    const best = daily.reduce((found, item) => ((Number(item[key]) || 0) > (Number(found[key]) || 0) ? item : found), daily[0]);
    const summary = Number(best[key]) ? `Best day: ${S.day(best.day)}, ${format(best[key])}.` : `No ${label.toLowerCase()} yet in this period.`;
    return h("figure", { class: "vid-chart" },
      h("div", { class: "vid-bars", role: "img", "aria-label": `${label} per day. ${summary}` },
        ...daily.map((item) => h("span", { class: "vid-bar", title: `${S.day(item.day)}: ${format(item[key])}`, style: { height: `${Math.max(2, percent(Number(item[key]) || 0, top))}%` } }))),
      h("figcaption", { class: "vid-meta", text: `${label} per day, ${S.day(daily[0].day)} to ${S.day(daily[daily.length - 1].day)}. ${summary}` }));
  }

  async function analyticsTab(panel, route, info) {
    const days = ["7", "28", "90", "365"].includes(route.query.days) ? route.query.days : "28";
    const data = await api(`/analytics?days=${days}`);
    const t = data.totals;
    panel.append(h("div", { class: "vid-row", style: { justifyContent: "space-between" } },
      h("div", { class: "vid-chips", role: "group", "aria-label": "Period" }, ...[["7", "7 days"], ["28", "28 days"], ["90", "90 days"], ["365", "A year"]].map(([id, label]) => h("button", { type: "button", class: `btn sm ${id === days ? "secondary" : "ghost"}`, "aria-pressed": String(id === days), text: label, onclick: (event) => goFrom(event, { tab: "analytics", days: id }) }))),
      S.button("Download CSV", () => Aira.download(`/api/videos/analytics?days=${days}&format=csv`, `videos-analytics-${days}-days.csv`), { kind: "secondary sm" })));
    const supportText = t.support.length ? t.support.map((item) => S.money(item.cents, item.currency)).join(" + ") : S.money(0, "USD");
    panel.append(S.kpis([
      { label: "Views", value: t.views.toLocaleString(), hint: change(t.views, t.previous_views) || "no views yet" },
      { label: "Watch time", value: watchTime(t.watch_seconds), hint: t.views ? `${clockText(t.average_seconds)} a view on average` : "" },
      { label: "New subscribers", value: t.new_subscribers.toLocaleString(), hint: `${subscribersText(t.subscribers)} in all` },
      { label: "Likes and comments", value: `${t.likes.toLocaleString()} · ${t.comments.toLocaleString()}` },
      adult(info.channel) ? { label: "Earned from ads", value: usd(t.earned_micros), hint: t.views ? `${usd(t.rpm_micros)} per 1,000 views` : "estimated until the month closes" } : null,
      adult(info.channel) ? { label: "Support received", value: supportText } : null,
      adult(info.channel) && (t.memberships.length || t.deals.length) ? { label: "Members and brand deals", value: [...t.memberships, ...t.deals].map((item) => S.money(item.cents, item.currency)).join(" + "), hint: "received in this period" } : null,
    ]));
    if (t.public_views) panel.append(h("p", { class: "vid-meta", text: `${t.public_views === 1 ? "1 view" : `${t.public_views.toLocaleString()} views`} came from people without an account, on your public video pages.` }));
    panel.append(S.grid(S.section("Views", dailyChart(data.daily, "views", "Views", (n) => viewsText(n))), adult(info.channel) ? S.section("Earned from ads", dailyChart(data.daily, "earned_micros", "Earnings", (n) => usd(n))) : S.section("Watch time", dailyChart(data.daily, "watch_seconds", "Watch time", (n) => watchTime(n)))));
    const byId = Object.fromEntries(data.videos.map((item) => [item.id, item]));
    const highlight = (id, title, hint) => (id && byId[id] ? h("div", { class: "card vid-highlight" }, h("span", { class: "vid-meta", text: title }), h("a", { href: `#/videos?watch=${id}`, class: "vid-mine-title", text: byId[id].title }), h("span", { class: "vid-meta", text: hint(byId[id]) })) : null);
    const highlights = [
      adult(info.channel) ? highlight(data.top_earner, "Earned the most", (v) => `${usd(v.earned_micros)} · ${viewsText(v.views)}`) : null,
      highlight(data.top_viewed, "Watched by the most people", (v) => `${viewsText(v.views)} · ${watchTime(v.watch_seconds)}`),
      highlight(data.most_watched, "Longest watch time", (v) => `${watchTime(v.watch_seconds)} · ${v.average_percent}% watched on average`),
      adult(info.channel) ? highlight(data.lowest_earner, "Earned the least", (v) => `${usd(v.earned_micros)} · ${viewsText(v.views)}. A clearer title, a better picture or chapters can help.`) : null,
    ].filter(Boolean);
    if (highlights.length) panel.append(h("div", { class: "vid-highlights" }, ...highlights));
    panel.append(S.section(adult(info.channel) ? "Every video, best earner first" : "Every video", S.table({
      caption: "Your videos in this period",
      columns: [
        { key: "title", label: "Video", render: (row) => h("a", { href: `#/videos?watch=${row.id}`, text: row.title }) },
        { key: "views", label: "Views", num: true },
        { key: "watch_seconds", label: "Watch time", num: true, render: (row) => watchTime(row.watch_seconds) },
        { key: "average_percent", label: "Watched on average", num: true, render: (row) => (row.duration ? `${row.average_percent}%` : "") },
        { key: "likes", label: "Likes", num: true },
        adult(info.channel) ? { key: "earned_micros", label: "Earned", num: true, render: (row) => usd(row.earned_micros) } : null,
        adult(info.channel) ? { key: "rpm_micros", label: "Per 1,000 views", num: true, render: (row) => usd(row.rpm_micros) } : null,
      ].filter(Boolean),
      rows: data.videos,
      empty: Aira.ui.empty("No videos yet", "Upload a video; its numbers appear here.", uploadLink(info, "Upload a video")),
    })));
  }

  // ---------------------------------------------------------------- moderation (the household's owner)
  async function moderationTab(panel, route) {
    const status = route.query.status || "open";
    const data = await api(`/moderation?status=${status}`);
    const act = async (item, action) => { await api(`/moderation/${item.id}`, { method: "POST", body: { action } }); Aira.router.render(); };
    panel.append(h("div", { class: "vid-chips", role: "group", "aria-label": "Reports" }, ...["open", "resolved", "dismissed"].map((name) => h("button", { type: "button", class: `btn sm ${name === status ? "secondary" : "ghost"}`, "aria-pressed": String(name === status), text: { open: "Open", resolved: "Resolved", dismissed: "Dismissed" }[name], onclick: (event) => goFrom(event, { tab: "moderation", status: name }) }))));
    panel.append(S.section(data.everyone ? "Reports from everyone on this computer" : "Reports about your household",
      data.reports.length ? h("ul", { class: "life-lines" }, ...data.reports.map((item) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
        h("div", { style: { flex: "1 1 260px", minWidth: "0" } },
          h("span", { class: "life-line-text", text: `${{ video: "A video", comment: "A comment", channel: "A channel" }[item.target_type]}${item.author ? ` by ${item.author.name}` : ""}` }),
          item.preview ? h("span", { class: "life-line-sub", text: `“${item.preview}”` }) : null,
          h("span", { class: "muted life-line-sub", text: `${item.reporter ? item.reporter.name : "Someone"}: ${item.reason} · ${when(item.created_at)}` })),
        status !== "open" ? S.pill(item.action || item.status) : null,
        status === "open" && item.target_type !== "channel" ? S.button(item.hidden ? "Restore" : "Hide it", () => act(item, item.hidden ? "restore" : "hide"), { kind: "sm" }) : null,
        status === "open" && item.can_suspend ? S.button("Suspend the channel", async () => { if (await Aira.confirm({ title: "Suspend this channel?", message: "Its videos and comments disappear for everyone and it cannot upload.", confirmLabel: "Suspend", danger: true })) act(item, "suspend"); }, { kind: "ghost sm" }) : null,
        status === "open" ? S.button("Nothing wrong", () => act(item, "dismiss"), { kind: "ghost sm" }) : null))) : Aira.ui.empty("Nothing to review", "Reports about videos, comments and channels appear here.")));
    if (data.everyone) {
      const decide = async (item, action) => { await api(`/moderation/notices/${item.id}`, { method: "POST", body: { action } }); Aira.router.render(); };
      const form = `${location.origin}/videos/copyright`;
      panel.append(S.section("Copyright notices",
        h("p", { class: "muted" }, h("span", { text: "Anyone can send one from the public form: " }), h("a", { href: form, target: "_blank", rel: "noopener", text: form })),
        S.table({
          columns: [
            { key: "video", label: "Video", render: (row) => row.video.title },
            { key: "claimant_name", label: "Claimed by", render: (row) => `${row.claimant_name}${row.claimant_organization ? ` (${row.claimant_organization})` : ""} · ${row.claimant_email}` },
            { key: "work", label: "Their work" },
            { key: "status", label: "Status", render: (row) => S.pill(NOTICE[row.status] || row.status) },
            { key: "actions", label: "", render: (row) => h("div", { class: "btn-row" },
              row.status === "received" ? S.button("Take down", () => decide(row, "takedown"), { kind: "sm" }) : null,
              row.status === "received" ? S.button("Reject", () => decide(row, "reject"), { kind: "ghost sm" }) : null,
              row.status === "counter_filed" ? S.button("Put it back", () => decide(row, "restore"), { kind: "sm" }) : null,
              row.status === "counter_filed" ? S.button("Keep it down", () => decide(row, "uphold"), { kind: "ghost sm" }) : null,
              row.status === "taken_down" ? S.button("Put it back", () => decide(row, "restore"), { kind: "ghost sm" }) : null) },
          ],
          rows: data.notices,
          empty: Aira.ui.empty("No notices", "Copyright notices appear here."),
        })));
    }
  }

  // ---------------------------------------------------------------- in the Aira frame: the folders, "+ New", the search
  // The tabs, which are also the left menu's folders; each names the page when it is open (the app's own name is in
  // the bar above, like a mail app's folder).
  const tabsFor = (info) => [
    { id: "home", label: "Home", icon: "today", about: "Popular and new videos, and the channels you follow" },
    { id: "reels", label: "Reels", icon: "vidReel", about: "Tall videos, one at a time: scroll, swipe or use the arrow keys" },
    { id: "films", label: "Short films", icon: "vidFilm", about: "Short films by genre, from people everywhere" },
    { id: "library", label: "Library", icon: "vidLibrary", about: "Your history, Watch later, playlists and subscriptions" },
    { id: "studio", label: info.unread ? `Studio (${info.unread})` : "Studio", icon: "studio", about: "Your channel, your uploads and what happened on them" },
    { id: "analytics", label: "Analytics", icon: "analytics", about: "How your videos are doing: views, watch time and subscribers" },
    adult(info.channel) ? { id: "earnings", label: "Earnings", icon: "vidEarn", about: "Every way you earn from your videos: ads, members, brand deals and support" } : null,
    info.moderator ? { id: "moderation", label: "Moderation", icon: "vidFlag", about: "Reports and copyright notices to review" } : null,
  ].filter(Boolean);
  const folders = (info, current) => { if (Aira.shell && Aira.shell.tabs) Aira.shell.tabs("videos", tabsFor(info), current); };

  // Who is using Aira: the channel once the page has been drawn, before that the household profile (life.js).
  const grownUp = () => {
    if (known) return adult(known.channel);
    const me = Aira.life && Aira.life.me;
    return !me || !me.age_group || ["adult", "senior"].includes(me.age_group);
  };
  const cloudCan = (permission) => !(Aira.cloud && typeof Aira.cloud.can === "function") || Aira.cloud.can(permission);
  // Where each "+ New" lands; the drawn page then opens its form.
  const NEW_PLACE = { video: { tab: "studio" }, reel: { tab: "studio" }, short: { tab: "studio" }, playlist: { tab: "library" }, deal: { tab: "earnings", part: "deals" } };

  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  // "+ New": what people make most here. A suspended channel uploads nothing; brand deals are for adults.
  Aira.appNew.videos = () => {
    if (!cloudCan("life.manage")) return [];
    const uploads = known && !canUpload(known) ? [] : [
      { label: "Upload a video", icon: "vidUpload", href: "#/videos?tab=studio&new=video" },
      { label: "Upload a reel", icon: "vidReel", href: "#/videos?tab=studio&new=reel" },
      { label: "Upload a short film", icon: "vidFilm", href: "#/videos?tab=studio&new=short" },
    ];
    return [
      ...uploads,
      { label: "New playlist", icon: "vidPlaylist", href: "#/videos?tab=library&new=playlist" },
      grownUp() ? { label: "New brand deal", icon: "vidDeal", href: "#/videos?tab=earnings&part=deals&new=deal" } : null,
    ].filter(Boolean);
  };
  const offered = () => Aira.appNew.videos().map((item) => item.label).join("|");

  const fold = (text) => String(text || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  Aira.appSearch.videos = {
    // a phone's box shows about a dozen letters: the logo beside it already says which app this is
    get placeholder() { return window.matchMedia && window.matchMedia("(max-width: 600px)").matches ? "Search" : "Search videos"; },
    // Up to six: channels, videos everyone can find and your own (private, link only, scheduled), your playlists.
    async suggest(query) {
      const q = query.trim().slice(0, 100);
      if (!q) return [];
      const needle = fold(q);
      const [found, mine, lists] = await Promise.all([cached(`/search?q=${encodeURIComponent(q)}`), cached(`/studio?q=${encodeURIComponent(q)}`), cached("/playlists", 30000)]);
      const seen = new Set();
      const videoRows = [...((found && found.videos) || []), ...((mine && mine.videos) || [])].filter((video) => !seen.has(video.id) && seen.add(video.id)).map((video) => ({
        label: video.title,
        sub: video.mine ? [`Your ${lower(KIND[video.kind] || "video")}`, video.visibility === "public" ? "" : VISIBILITY[video.visibility], video.scheduled ? "scheduled" : viewsText(video.views)].filter(Boolean).join(" · ") : [KIND[video.kind] || "Video", video.channel.name, viewsText(video.views)].join(" · "),
        icon: KIND_ICON[video.kind] || "videos",
        href: `#/videos?watch=${video.id}`,
      }));
      const channelRows = ((found && found.channels) || []).map((channel) => ({ label: channel.name, sub: `Channel · ${subscribersText(channel.subscribers)}`, icon: "recruiters", href: `#/videos?channel=${channel.id}` }));
      const listRows = ((lists && lists.playlists) || []).filter((item) => fold(item.name).includes(needle)).map((item) => ({ label: item.name, sub: `Playlist · ${item.count === 1 ? "1 video" : `${item.count} videos`}`, icon: "vidPlaylist", href: `#/videos?playlist=${item.id}` }));
      return [...channelRows.slice(0, 2), ...videoRows.slice(0, 3), ...listRows.slice(0, 1), ...videoRows.slice(3), ...channelRows.slice(2), ...listRows.slice(1)].slice(0, 6);
    },
    // Enter: every match, on Home.
    run(query) { const q = query.trim().slice(0, 100); go(q ? { tab: "home", q } : { tab: "home" }); },
  };

  async function fulfil(ask, info) {
    if (KIND[ask]) { if (canUpload(info) && hooks.upload) hooks.upload(ask); return; }
    if (ask === "playlist") { newPlaylist(); return; }
    if (ask === "deal" && hooks.deal) await hooks.deal();
  }

  // ---------------------------------------------------------------- the view
  Aira.views.videos = async (main, route) => {
    const query = route.query || {};
    // "+ New" asks with ?new=<kind>: remember the ask, take it out of the address (Back or a refresh must not ask
    // again) and draw the page without it; the drawn page then answers it.
    if (query.new) {
      pending = NEW_PLACE[query.new] ? String(query.new) : "";
      const rest = { ...query, ...(NEW_PLACE[query.new] || {}) };
      delete rest.new;
      Aira.router.go(["videos"], rest, { replace: true });
      return;
    }
    const ask = query.upload ? "video" : pending;
    pending = "";
    for (const key of Object.keys(hooks)) delete hooks[key];
    let info;
    try { info = await api("/me"); } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    const before = offered();
    known = info;
    if (offered() !== before && Aira.renderNav) Aira.renderNav(); // "+ New" now knows the channel (suspended, a child)
    try {
      if (query.watch) { folders(info, null); await watchPage(main, info, query.watch); return; }
      if (query.channel) { folders(info, null); await channelPage(main, info, query.channel); return; }
      if (query.playlist) { folders(info, "library"); await playlistPage(main, info, query.playlist); return; }
    } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    const tabs = tabsFor(info);
    const current = tabs.find((item) => item.id === query.tab) || tabs[0];
    const searching = current.id === "home" && query.q;
    // On a phone the menu (and its "+ New") is folded away, so the page keeps its own Upload there.
    const body = S.page(main, {
      title: searching ? `Results for “${String(query.q).slice(0, 100)}”` : current.id === "studio" ? "Studio" : current.label,
      subtitle: searching ? "Videos, reels, short films and channels" : current.about,
      actions: [canUpload(info) && current.id !== "studio" ? S.button("Upload", () => go({ tab: "studio", new: "video" }), { kind: "primary vid-phone-new", icon: "plus" }) : null],
    });
    body.classList.add("vid-page");
    const tab = S.tabs(body, route, "videos", tabs, current.id);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    try {
      if (tab === "reels") await reelsTab(panel, info, route);
      else if (tab === "films") await filmsTab(panel, info, route);
      else if (tab === "library") await libraryTab(panel, info);
      else if (tab === "studio") await studioTab(panel, info, route);
      else if (tab === "analytics") await analyticsTab(panel, route, info);
      else if (tab === "earnings") await earningsTab(panel, info, route);
      else if (tab === "moderation") await moderationTab(panel, route);
      else await homeTab(panel, info, route);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); }
    restoreFocus(main);
    if (ask && main.isConnected) fulfil(ask, info).catch((error) => Aira.reportError(error));
  };
})();

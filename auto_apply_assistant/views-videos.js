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
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("videos", path, opts);
  const go = (query) => Aira.router.go(["videos"], query);

  const CATEGORY = { film: "Film and animation", music: "Music", comedy: "Comedy", education: "Learning", gaming: "Gaming", sports: "Sports", news: "News", howto: "How-to", travel: "Travel", food: "Food", family: "Family", animals: "Animals", science: "Science and tech", other: "Other" };
  const GENRE = { drama: "Drama", comedy: "Comedy", documentary: "Documentary", animation: "Animation", horror: "Horror", thriller: "Thriller", romance: "Romance", scifi: "Science fiction", experimental: "Experimental", other: "Other" };
  const RATING = { everyone: "For everyone", teen: "Teens and older", mature: "Adults only" };
  const VISIBILITY = { public: "Everyone (listed)", unlisted: "Anyone with the link", private: "Only me" };
  const KIND = { video: "Video", reel: "Reel", short: "Short film" };
  const BLOCKED = { hidden: "Hidden by a moderator", taken_down: "Taken down after a copyright notice", suspended: "Your channel is suspended" };
  const NOTICE = { received: "Received", taken_down: "Taken down", rejected: "Rejected", counter_filed: "Counter-notice sent", restored: "Back up", upheld: "Kept down" };
  const LANGUAGES = [["en", "English"], ["te", "Telugu"], ["hi", "Hindi"], ["ta", "Tamil"], ["kn", "Kannada"], ["ml", "Malayalam"], ["mr", "Marathi"], ["bn", "Bengali"], ["gu", "Gujarati"], ["pa", "Punjabi"], ["ur", "Urdu"], ["es", "Spanish"], ["fr", "French"], ["de", "German"], ["ar", "Arabic"], ["zh", "Chinese"], ["ja", "Japanese"], ["pt", "Portuguese"]];
  const options = (labels) => Object.entries(labels);

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
        if (failures > 3 || (error && error.status && error.status !== 400 && error.status < 500)) { if (error.status === 404) Aira.remember(key, ""); throw error; }
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
    else box.append(h("span", { text: KIND[video.kind] || "Video" }));
    if (video.kind !== "video") box.append(h("span", { class: "vid-kind", text: KIND[video.kind] }));
    if (video.duration) box.append(h("span", { class: "vid-badge", text: clockText(video.duration) }));
    return box;
  }
  function card(video, { sub, open } = {}) {
    return h("button", { type: "button", class: "vid-card", "aria-label": `${video.title}, ${video.channel.name}`, onclick: () => (open ? open(video) : go({ watch: video.id })) },
      thumb(video),
      h("span", { class: "vid-card-title", text: video.title }),
      h("span", { class: "vid-meta", text: sub || `${video.channel.name} · ${viewsText(video.views)} · ${when(video.created_at)}` }));
  }
  const grid = (videos, empty, sub) => (videos && videos.length ? h("div", { class: "vid-grid" }, ...videos.map((video) => card(video, { sub: sub ? sub(video) : undefined }))) : Aira.ui.empty(empty[0], empty[1]));

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
    const { video } = await api(`/videos/${id}`);
    const body = S.page(main, { title: video.title, subtitle: `${video.channel.name} · ${viewsText(video.views)} · ${when(video.created_at)}`, actions: [S.button("Back to Videos", () => go({}), { kind: "ghost" })] });
    const { box, el } = player(video);
    const note = h("p", { class: "muted", hidden: true });
    if (video.mime && el.canPlayType && el.canPlayType(video.mime) === "") { note.hidden = false; note.textContent = `This browser may not play this file. ${video.playback || ""}`; }
    if (video.blocked) body.append(h("div", { class: "card", role: "status", style: { borderColor: "var(--warning)", padding: "12px" }, text: `${BLOCKED[video.blocked] || "Not shown to others"}. Only you can watch it.` }));
    body.append(box, note);
    track(el, video.id);
    const start = async () => {
      if (!video.mine && video.ads_enabled) {
        const ad = await serveAd(video.id, "preroll");
        if (ad && ad.media_url) { el.hidden = true; await playPreroll(box, ad); el.hidden = false; }
      }
      el.src = video.stream_url;
      el.play().catch(() => null);
    };
    start();
    if (!video.mine && video.ads_enabled) serveAd(video.id, "banner").then((ad) => { if (ad) box.after(bannerNode(ad)); });

    const likeButton = S.button(video.liked ? `Liked (${video.likes})` : `Like (${video.likes})`, async () => {
      const result = await api(`/videos/${video.id}/like`, { method: "POST", body: { on: !video.liked } });
      video.liked = result.liked; video.likes = result.likes;
      likeButton.querySelector("span").textContent = video.liked ? `Liked (${video.likes})` : `Like (${video.likes})`;
      likeButton.setAttribute("aria-pressed", String(video.liked));
    }, { kind: video.liked ? "secondary" : "ghost" });
    likeButton.setAttribute("aria-pressed", String(!!video.liked));
    const subscribeButton = video.mine ? null : S.button(video.subscribed ? "Subscribed" : "Subscribe", async () => {
      const result = await api(`/channels/${video.channel.id}/subscribe`, { method: "POST", body: { on: !video.subscribed } });
      video.subscribed = result.subscribed;
      subscribeButton.querySelector("span").textContent = video.subscribed ? "Subscribed" : "Subscribe";
    }, { kind: video.subscribed ? "secondary" : "primary" });
    const saveButton = S.button(video.saved ? "Saved for later" : "Watch later", async () => {
      const result = await api(`/videos/${video.id}/later`, { method: "POST", body: { on: !video.saved } });
      video.saved = result.saved;
      saveButton.querySelector("span").textContent = video.saved ? "Saved for later" : "Watch later";
    }, { kind: "ghost" });
    body.append(h("div", { class: "card suite-section" },
      h("div", { class: "vid-row", style: { justifyContent: "space-between" } },
        h("div", { class: "vid-row" }, face(video.channel), h("div", {}, channelLink(video.channel), h("div", { class: "vid-meta", text: subscribersText(video.subscribers) }))),
        subscribeButton),
      h("div", { class: "vid-row" }, likeButton, saveButton,
        S.button("Add to playlist", () => addToPlaylist(video), { kind: "ghost" }),
        S.button("Share", () => share(video), { kind: "ghost" }),
        video.mine ? null : S.button("Report", () => report(info, "video", video.id), { kind: "ghost" })),
      h("div", { class: "vid-chips" }, h("span", { class: "suite-pill", text: RATING[video.rating] }), h("span", { class: "suite-pill", text: CATEGORY[video.category] || video.category }), video.kind !== "video" ? h("span", { class: "suite-pill", text: KIND[video.kind] }) : null, video.genre ? h("span", { class: "suite-pill", text: GENRE[video.genre] || video.genre }) : null),
      video.description ? h("p", { style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: "0" }, text: video.description }) : null,
      video.credits && video.credits.length ? h("dl", { class: "kv" }, ...video.credits.flatMap((item) => [h("dt", { text: item.role || "Credit" }), h("dd", { text: item.name })])) : null,
      video.tags && video.tags.length ? h("p", { class: "vid-meta", text: video.tags.map((tag) => `#${tag}`).join(" ") }) : null,
      h("p", { class: "vid-meta", text: `Video id: ${video.id}` })));
    body.append(await commentsSection(info, video));
    try {
      const more = await api(`/home?category=${encodeURIComponent(video.category)}`);
      const next = [...more.popular, ...more.recent].filter((item, index, all) => item.id !== video.id && all.findIndex((other) => other.id === item.id) === index).slice(0, 8);
      if (next.length) body.append(S.section("Up next", grid(next, ["", ""])));
    } catch { /* the list is extra */ }
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
    const parts = [h("p", { class: "muted", text: "People who use Aira can open this address." }), h("div", { class: "vid-row" }, h("code", { style: { overflowWrap: "anywhere" }, text: inApp }), S.button("Copy", () => S.copy(inApp), { kind: "secondary sm" }))];
    if (video.mine && video.visibility !== "private" && video.rating !== "mature" && !video.blocked) {
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
  function searchBar(route) {
    const input = h("input", { type: "search", value: route.query.q || "", placeholder: "Search videos and channels", "aria-label": "Search videos and channels" });
    const form = h("form", { class: "vid-row", role: "search", onsubmit: (event) => { event.preventDefault(); go(input.value.trim() ? { tab: "home", q: input.value.trim() } : { tab: "home" }); } }, h("div", { style: { flex: "1 1 240px" } }, input), h("button", { type: "submit", class: "btn primary", text: "Search" }));
    return form;
  }

  async function homeTab(panel, info, route) {
    panel.append(searchBar(route));
    if (route.query.q) {
      const found = await api(`/search?q=${encodeURIComponent(route.query.q)}`);
      if (found.channels.length) panel.append(S.section("Channels", h("div", { class: "vid-chips" }, ...found.channels.map((channel) => h("a", { class: "btn ghost", href: `#/videos?channel=${channel.id}` }, h("span", { text: `${channel.avatar} ${channel.name}` }))))));
      panel.append(S.section(`Results for “${route.query.q}”`, grid(found.videos, ["Nothing found", "Try other words, or look through the categories."])));
      return;
    }
    const category = route.query.category || "";
    const chips = h("div", { class: "vid-chips", role: "group", "aria-label": "Categories" },
      h("button", { type: "button", class: `btn sm ${category ? "ghost" : "secondary"}`, "aria-pressed": String(!category), text: "All", onclick: () => go({ tab: "home" }) }),
      ...info.categories.map((id) => h("button", { type: "button", class: `btn sm ${category === id ? "secondary" : "ghost"}`, "aria-pressed": String(category === id), text: CATEGORY[id] || id, onclick: () => go({ tab: "home", category: id }) })));
    panel.append(chips);
    const data = await api(`/home${category ? `?category=${category}` : ""}`);
    if (data.subscriptions.length) panel.append(S.section("From your subscriptions", grid(data.subscriptions, ["", ""])));
    if (data.reels.length) panel.append(S.section("Reels", h("div", { class: "vid-grid" }, ...data.reels.map((reel) => card(reel, { open: () => go({ tab: "reels", start: reel.id }) })))));
    panel.append(S.section("Popular", grid(data.popular, ["No videos yet", "Be the first: upload a video from Studio."]), h("p", { class: "vid-meta", text: `How Popular is worked out: ${data.ranking}. Nothing is paid for and nothing is personal.` })));
    panel.append(S.section("New", grid(data.recent, ["No videos yet", "Videos people upload appear here."])));
  }

  async function filmsTab(panel, info, route) {
    const genre = route.query.genre || "";
    const data = await api(`/films${genre ? `?genre=${genre}` : ""}`);
    panel.append(h("div", { class: "vid-chips", role: "group", "aria-label": "Genres" },
      h("button", { type: "button", class: `btn sm ${genre ? "ghost" : "secondary"}`, text: "All", onclick: () => go({ tab: "films" }) }),
      ...data.genres.map((id) => h("button", { type: "button", class: `btn sm ${genre === id ? "secondary" : "ghost"}`, text: GENRE[id] || id, onclick: () => go({ tab: "films", genre: id }) }))));
    panel.append(S.section("Short films", grid(data.films, ["No short films yet", "Upload one from Studio: choose Short film, then its genre and credits."], (film) => `${film.channel.name} · ${GENRE[film.genre] || ""} · ${clockText(film.duration)}`)));
  }

  async function channelPage(main, info, id) {
    const data = await api(`/channels/${id}`);
    const channel = data.channel;
    const body = S.page(main, { title: channel.name, subtitle: subscribersText(channel.subscribers), actions: [S.button("Back to Videos", () => go({}), { kind: "ghost" })] });
    const subscribe = channel.mine ? null : S.button(channel.subscribed ? "Subscribed" : "Subscribe", async () => {
      const result = await api(`/channels/${channel.id}/subscribe`, { method: "POST", body: { on: !channel.subscribed } });
      channel.subscribed = result.subscribed;
      subscribe.querySelector("span").textContent = channel.subscribed ? "Subscribed" : "Subscribe";
    }, { kind: channel.subscribed ? "secondary" : "primary" });
    body.append(S.section("", h("div", { class: "vid-row" }, face(channel), h("strong", { text: channel.name }), subscribe, channel.mine ? null : S.button("Report", () => report(info, "channel", channel.id), { kind: "ghost sm" })), channel.about ? h("p", { style: { whiteSpace: "pre-wrap" }, text: channel.about }) : null));
    body.append(S.section("Videos", grid(data.videos, ["No videos yet", "This channel has not shared anything yet."])));
  }

  async function playlistPage(main, info, id) {
    const { playlist } = await api(`/playlists/${id}`);
    const body = S.page(main, { title: playlist.name, subtitle: playlist.count === 1 ? "1 video" : `${playlist.count} videos`, actions: [
      S.button("Rename", () => S.edit({ title: "Rename playlist", fields: [{ name: "name", label: "Name", required: true }], values: playlist, onSave: async (values) => { await api(`/playlists/${playlist.id}`, { method: "PATCH", body: values }); Aira.router.render(); } }), { kind: "ghost" }),
      S.button("Delete", async () => { if (await S.confirmDelete("this playlist")) { await api(`/playlists/${playlist.id}`, { method: "DELETE" }); go({ tab: "library" }); } }, { kind: "ghost" }),
      S.button("Back to Library", () => go({ tab: "library" }), { kind: "ghost" })] });
    body.append(playlist.videos.length ? h("div", { class: "vid-grid" }, ...playlist.videos.map((video) => h("div", { class: "suite-body", style: { gap: "6px" } }, card(video), S.button("Remove", async () => { await api(`/playlists/${playlist.id}/videos/${video.id}`, { method: "DELETE" }); Aira.router.render(); }, { kind: "ghost sm" })))) : Aira.ui.empty("This playlist is empty", "Open a video and choose Add to playlist."));
  }

  async function libraryTab(panel) {
    const [history, later, lists, subs] = await Promise.all([api("/history"), api("/later"), api("/playlists"), api("/subscriptions")]);
    panel.append(S.section("History", history.videos.length ? h("div", { class: "btn-row" }, S.button("Clear history", async () => { if (await Aira.confirm({ title: "Clear your history?", message: "Videos you watched are removed from this list. It does not change any view counts.", confirmLabel: "Clear" })) { await api("/history", { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" })) : null,
      grid(history.videos, ["Nothing watched yet", "Videos you watch appear here, so you can carry on where you stopped."], (video) => `${video.channel.name} · watched ${when(video.watched_at)}`)));
    panel.append(S.section("Watch later", grid(later.videos, ["Nothing saved", "Press Watch later under a video to keep it here."])));
    const newList = S.button("New playlist", () => S.edit({ title: "New playlist", fields: [{ name: "name", label: "Name", required: true }], onSave: async (values) => { const made = (await api("/playlists", { method: "POST", body: values })).playlist; go({ playlist: made.id }); } }), { kind: "secondary sm" });
    panel.append(S.section("Playlists", h("div", { class: "btn-row" }, newList), lists.playlists.length ? h("div", { class: "vid-chips" }, ...lists.playlists.map((item) => h("a", { class: "btn ghost", href: `#/videos?playlist=${item.id}`, text: `${item.name} (${item.count})` }))) : h("p", { class: "muted", text: "Make playlists to keep videos together." })));
    panel.append(S.section("Subscriptions", subs.channels.length ? h("div", { class: "vid-chips" }, ...subs.channels.map((channel) => h("a", { class: "btn ghost", href: `#/videos?channel=${channel.id}`, text: `${channel.avatar} ${channel.name}` }))) : h("p", { class: "muted", text: "Subscribe to channels you like to see their new videos first." })));
  }

  // ---------------------------------------------------------------- reels: one tall video at a time
  async function reelsTab(panel, info, route) {
    const { reels } = await api(`/reels${route.query.start ? `?start=${route.query.start}` : ""}`);
    if (!reels.length) { panel.append(Aira.ui.empty("No reels yet", "A reel is a tall video of up to 90 seconds. Upload one from Studio.")); return; }
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
      const el = h("video", { src: item.ad ? item.ad.media_url : item.video.stream_url, autoplay: true, loop: !item.ad, playsinline: true, "aria-label": item.ad ? `Sponsored reel from ${item.ad.advertiser}` : item.video.title });
      el.muted = muted;
      const info_ = item.ad
        ? h("div", { class: "vid-reel-info" }, h("div", { class: "vid-row" }, h("span", { class: "vid-ad-label", text: "Sponsored" }), h("strong", { text: item.ad.advertiser })), h("span", { text: item.ad.headline }), h("div", {}, adLink(item.ad, "primary sm")))
        : h("div", { class: "vid-reel-info" }, h("strong", { text: item.video.title }), h("a", { href: `#/videos?channel=${item.video.channel.id}`, text: item.video.channel.name }), h("span", { text: `${viewsText(item.video.views)} · ${likesText(item.video.likes)}` }));
      const reel = h("div", { class: "vid-reel" }, el, info_);
      if (item.ad) {
        let reported = false;
        el.addEventListener("timeupdate", () => { if (!reported && el.currentTime >= item.ad.seen_after) { reported = true; seenAd(item.ad); } });
      } else track(el, item.video.id);
      const soundButton = S.button(muted ? "Sound on" : "Sound off", () => { muted = !muted; el.muted = muted; soundButton.querySelector("span").textContent = muted ? "Sound on" : "Sound off"; }, { kind: "ghost" });
      const actions = h("div", { class: "vid-reel-actions" },
        S.button("Previous", () => show(index - 1), { kind: "ghost" }),
        soundButton,
        item.ad ? null : S.button("Like", async () => { const result = await api(`/videos/${item.video.id}/like`, { method: "POST", body: { on: true } }); item.video.likes = result.likes; Aira.toast("Liked.", "success"); }, { kind: "ghost" }),
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
    ].filter(Boolean), { kind: "video", category: "other", rating: "everyone", visibility: "public", language: info.channel.language || "en" });
    const filmForm = S.form([
      { name: "genre", label: "Genre", type: "select", options: options(GENRE) },
      { name: "credits", label: "Credits (one per line, like Director: Asha Rao)", type: "textarea", rows: 3 },
    ], { genre: "drama" });
    filmForm.node.hidden = true;
    form.inputs.kind.control.addEventListener("change", () => { filmForm.node.hidden = form.inputs.kind.control.value !== "short"; });

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
      if (chosen.size > limits.video_bytes) { facts.textContent = `This file is too large: the most is ${Aira.fmt.bytes(limits.video_bytes)}.`; return; }
      objectUrl = URL.createObjectURL(chosen);
      preview.src = objectUrl;
      frameArea.hidden = false;
      meta = await readMetadata(preview);
      if (meta.duration) preview.currentTime = Math.min(1, meta.duration / 2);
      const tall = meta.height > meta.width;
      facts.textContent = `${chosen.name} · ${Aira.fmt.bytes(chosen.size)}${meta.duration ? ` · ${clockText(meta.duration)}` : ""}${meta.width ? ` · ${meta.width}×${meta.height}${tall ? " (tall)" : ""}` : ""}`;
      if (!form.inputs.title.control.value) form.inputs.title.control.value = chosen.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 100);
      if (tall && meta.duration && meta.duration <= limits.reel_seconds) form.inputs.kind.control.value = "reel";
    });

    const bar = h("div", { class: "vid-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0", hidden: true }, h("span", { style: { width: "0%" } }));
    const status = h("p", { class: "muted", "aria-live": "polite" });
    const setProgress = (fraction, text) => { const pct = Math.round(fraction * 100); bar.hidden = false; bar.setAttribute("aria-valuenow", String(pct)); bar.firstChild.style.width = `${pct}%`; status.textContent = text; };
    const send = S.button("Upload", async () => {
      form.setError("");
      const chosen = file.files && file.files[0];
      if (!chosen) { form.setError("Choose a video first."); return; }
      const values = { ...form.read(), ...(form.inputs.kind.control.value === "short" ? filmForm.read() : {}) };
      if (!values.title) { form.setError("Enter a title."); return; }
      if (values.kind === "reel" && meta.duration > limits.reel_seconds + 0.5) { form.setError(`A reel is at most ${limits.reel_seconds} seconds long.`); return; }
      if (values.kind === "reel" && meta.width && meta.height <= meta.width) { form.setError("A reel is a tall (portrait) video."); return; }
      try {
        const total = Aira.fmt.bytes(chosen.size);
        const video = await uploadFile(chosen, "video", { onProgress: (f) => setProgress(f * 0.9, `Uploading ${Math.round(f * 100)}% of ${total}`) });
        let thumbId = "";
        const picture = (pictureFile.files && pictureFile.files[0]) || frameBlob || (meta.width ? await captureFrame(preview, Math.min(1, (meta.duration || 2) / 2)) : null);
        if (picture) { setProgress(0.93, "Saving the picture"); thumbId = (await uploadFile(picture instanceof File ? picture : new File([picture], "frame.jpg", { type: "image/jpeg" }), "image")).id; }
        let captionsId = "";
        if (captions.files && captions.files[0]) { setProgress(0.96, "Saving the captions"); captionsId = (await uploadFile(captions.files[0], "captions")).id; }
        const created = (await api("/videos", { method: "POST", body: { ...values, file_id: video.id, thumb_file_id: thumbId, captions_file_id: captionsId, duration: meta.duration, width: meta.width, height: meta.height } })).video;
        setProgress(1, "Done.");
        Aira.toast("Your video is up.", "success");
        go({ watch: created.id });
      } catch (error) { form.setError(error && error.message ? error.message : String(error)); status.textContent = "You can press Upload again: it carries on from where it stopped."; }
    }, { kind: "primary" });
    return S.section("Upload a video",
      h("p", { class: "muted", text: `MP4 plays everywhere; WebM plays in most browsers. Up to ${Aira.fmt.bytes(limits.video_bytes)}. A reel is tall and at most ${limits.reel_seconds} seconds.` }),
      Aira.ui.field("Video file", file), facts, frameArea, Aira.ui.field("Or upload a picture instead", pictureFile), form.node, filmForm.node, Aira.ui.field("Captions (WebVTT, optional)", captions), bar, status, h("div", { class: "btn-row" }, send));
  }

  function editVideo(video) {
    return S.edit({
      title: "Edit video",
      fields: [
        { name: "title", label: "Title", required: true },
        { name: "description", label: "Description", type: "textarea", rows: 3 },
        { name: "category", label: "Category", type: "select", options: options(CATEGORY) },
        { name: "tags", label: "Tags (separate them with commas)" },
        { name: "rating", label: "Who is it suitable for?", type: "select", options: options(RATING) },
        { name: "visibility", label: "Who can watch it?", type: "select", options: options(VISIBILITY) },
        { name: "for_kids", label: "Made for children", type: "checkbox" },
        { name: "ads_enabled", label: "Allow ads on this video (when your channel earns)", type: "checkbox" },
      ],
      values: { ...video, tags: (video.tags || []).join(", ") },
      onSave: async (values) => { await api(`/videos/${video.id}`, { method: "PATCH", body: values }); Aira.toast("Saved.", "success"); Aira.router.render(); },
    });
  }

  async function studioTab(panel, info) {
    const [studio, notes] = await Promise.all([api("/studio"), api("/notifications")]);
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
      S.section("Your channel", h("p", { class: "muted", text: `${subscribersText(studio.subscribers)} · ${studio.videos.length === 1 ? "1 video" : `${studio.videos.length} videos`}` }), channelForm.node,
        h("div", { class: "btn-row" }, S.button("Save channel", async () => { try { await api("/me", { method: "PATCH", body: channelForm.read() }); Aira.toast("Saved.", "success"); } catch (error) { channelForm.setError(error.message); } }, { kind: "secondary" }), h("a", { class: "btn ghost", href: `#/videos?channel=${channel.id}`, text: "See your channel" }))),
      uploadSection(info)));
    panel.append(S.section("Your videos", S.table({
      columns: [
        { key: "title", label: "Title", render: (row) => h("a", { href: `#/videos?watch=${row.id}`, text: row.title }) },
        { key: "kind", label: "Kind", render: (row) => KIND[row.kind] },
        { key: "visibility", label: "Who can watch", render: (row) => (row.blocked ? h("span", { class: "suite-pill bad", text: BLOCKED[row.blocked] || row.blocked }) : VISIBILITY[row.visibility]) },
        { key: "views", label: "Views", num: true },
        { key: "likes", label: "Likes", num: true },
        { key: "actions", label: "", render: (row) => h("div", { class: "btn-row" }, S.button("Edit", () => editVideo(row), { kind: "ghost sm" }), S.button("Share", async () => share({ ...(await api(`/videos/${row.id}`)).video }), { kind: "ghost sm" }), S.button("Delete", async () => { if (await S.confirmDelete(`“${row.title}”`)) { await api(`/videos/${row.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" })) },
      ],
      rows: studio.videos,
      empty: Aira.ui.empty("No videos yet", "Upload your first video above."),
    })));
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

  // ---------------------------------------------------------------- earnings (the creator's side of ads)
  async function earningsTab(panel) {
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
        h("p", { text: `Businesses pay for the ads shown on your videos. You get ${rules.revenue_share_percent}% of what they pay; Aira keeps the rest.` }),
        h("p", { text: "An ad counts when it plays or is on screen for 2 seconds. Your own views, your household's views and repeated views do not count." }),
        h("p", { text: `Earnings are estimated until the month is closed and checked for invalid traffic. Then they are confirmed, and you can ask to be paid once you have at least ${usd(rules.min_payout_micros)}.` }),
        !data.terms_accepted_at ? h("p", { class: "muted", text: "Creator terms: you own or may use everything in your videos; you do not buy, swap or fake views or clicks; earnings from invalid traffic are taken back; you give correct payment details; Aira can stop ads on a channel that breaks these rules." }) : null)));
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

  // ---------------------------------------------------------------- moderation (the household's owner)
  async function moderationTab(panel, route) {
    const status = route.query.status || "open";
    const data = await api(`/moderation?status=${status}`);
    const act = async (item, action) => { await api(`/moderation/${item.id}`, { method: "POST", body: { action } }); Aira.router.render(); };
    panel.append(h("div", { class: "vid-chips" }, ...["open", "resolved", "dismissed"].map((name) => h("button", { type: "button", class: `btn sm ${name === status ? "secondary" : "ghost"}`, text: { open: "Open", resolved: "Resolved", dismissed: "Dismissed" }[name], onclick: () => go({ tab: "moderation", status: name }) }))));
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

  // ---------------------------------------------------------------- the view
  Aira.views.videos = async (main, route) => {
    let info;
    try { info = await api("/me"); } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    const query = route.query || {};
    try {
      if (query.watch) { await watchPage(main, info, query.watch); return; }
      if (query.channel) { await channelPage(main, info, query.channel); return; }
      if (query.playlist) { await playlistPage(main, info, query.playlist); return; }
    } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    const tabs = [
      { id: "home", label: "Home" },
      { id: "reels", label: "Reels" },
      { id: "films", label: "Short films" },
      { id: "library", label: "Library" },
      { id: "studio", label: info.unread ? `Studio (${info.unread})` : "Studio" },
      adult(info.channel) ? { id: "earnings", label: "Earnings" } : null,
      info.moderator ? { id: "moderation", label: "Moderation" } : null,
    ].filter(Boolean);
    const body = S.page(main, { title: "Videos", subtitle: "Watch, upload and earn", actions: [S.button("Upload", () => go({ tab: "studio" }), { kind: "primary" })] });
    const tab = S.tabs(body, route, "videos", tabs, tabs.some((item) => item.id === query.tab) ? query.tab : "home");
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    try {
      if (tab === "reels") await reelsTab(panel, info, route);
      else if (tab === "films") await filmsTab(panel, info, route);
      else if (tab === "library") await libraryTab(panel, info);
      else if (tab === "studio") await studioTab(panel, info);
      else if (tab === "earnings") await earningsTab(panel, info);
      else if (tab === "moderation") await moderationTab(panel, route);
      else await homeTab(panel, info, route);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); }
  };
})();

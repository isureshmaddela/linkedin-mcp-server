/* Aira — the public video page (/p/<token>; aira/suite/videos/videos_routes.py video_page).
 *
 * Anyone with the link watches without an account. The video, its picture and captions come from
 * /api/public/<token>/media/<file>, in ranges so seeking works. After a meaningful watch (30 seconds, or 30% of a
 * short video) the page tells the server once ("watched"), which counts one view a day per visitor. Public pages
 * show no ads. A copyright owner can report the video from the link under it.
 */
"use strict";
(() => {
  if (!window.AiraPublic) return;
  const clock = (seconds) => {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const minutes = Math.floor(total / 60), secs = total % 60;
    return `${minutes}:${String(secs).padStart(2, "0")}`;
  };
  window.AiraPublic.register("videos", "video", (root, response, ctx) => {
    const { h } = ctx;
    // public.js hands over the whole answer; the video's own data is in "page" (reading the answer itself made
    // every shared video say it was not available).
    const data = response.page || {};
    if (!data.available || !data.video) {
      root.append(h("div", { class: "pub-card" }, h("h1", { text: data.title || "This video is not available." }), h("p", { class: "pub-muted", text: "It may have been made private or removed." })));
      return;
    }
    const video = data.video;
    const tall = video.kind === "reel" || video.height > video.width;
    const player = h("video", { controls: true, playsinline: true, preload: "metadata", poster: video.thumb_url || undefined, src: video.stream_url, "aria-label": video.title, style: { display: "block", width: tall ? "auto" : "100%", maxWidth: "100%", height: tall ? "70vh" : "auto", maxHeight: "72vh", margin: "0 auto", background: "#000", borderRadius: "12px" } });
    if (video.captions_url) player.append(h("track", { kind: "captions", src: video.captions_url, srclang: "en", label: "Captions", default: true }));
    let watched = 0, last = null, sent = false;
    const need = Math.min(30, 0.3 * (Number(video.duration) || 100));
    player.addEventListener("timeupdate", () => {
      const now = player.currentTime;
      if (last !== null && !player.paused) { const step = now - last; if (step > 0 && step < 2) watched += step; }
      last = now;
      if (!sent && watched >= need) { sent = true; ctx.action("watched", { watched: Math.round(watched) }).catch(() => { sent = false; }); }
    });
    player.addEventListener("seeking", () => { last = null; });
    // Chapters written in the description: a button for each moment.
    const chapters = video.chapters && video.chapters.length
      ? h("div", { class: "pub-slots", role: "group", "aria-label": "Chapters" }, ...video.chapters.map((chapter) => h("button", { type: "button", class: "pub-slot", text: `${clock(chapter.at)} ${chapter.title}`, onclick: () => { player.currentTime = chapter.at; player.play().catch(() => null); } })))
      : null;
    const views = Number(video.views) === 1 ? "1 view" : `${Number(video.views || 0).toLocaleString()} views`;
    root.append(
      h("div", { class: "pub-card" }, player,
        h("h1", { text: video.title, style: { marginBottom: "4px" } }),
        h("p", { class: "pub-muted", text: `${video.channel.avatar} ${video.channel.name} · ${views}${video.duration ? ` · ${clock(video.duration)}` : ""}` }),
        video.paid_promotion ? h("p", {}, h("span", { class: "pub-badge", text: "Includes paid promotion" })) : null,
        chapters,
        video.description ? h("p", { text: video.description, style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere" } }) : null,
        video.credits && video.credits.length ? h("ul", {}, ...video.credits.map((item) => h("li", { text: `${item.role ? `${item.role}: ` : ""}${item.name}` }))) : null),
      h("p", { class: "pub-muted" }, h("a", { href: `/videos/copyright?video=${video.id}`, text: "Report a copyright problem" }), h("span", { text: ` · Video id: ${video.id}` })));
  });
})();

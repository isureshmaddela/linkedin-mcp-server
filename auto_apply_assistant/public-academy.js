/* Aira — Courses & Events: the pages customers and partners open without signing in (aira/suite/academy).
 *
 * academy/course       the course page: curriculum, free previews, rating and reviews, price and the enrolment
 *                      form; a partner's code in the address (?ref=CODE) goes with the enrolment
 * academy/student      a student's own page (their link is their access): lessons, progress, quizzes graded on
 *                      the server (the right options are never sent before a pass), the way to pay, a review of
 *                      the course, the certificate
 * academy/certificate  a certificate anyone can verify, ready to print
 * academy/catalog      every published course and upcoming event of the business on one page (passes ?ref on)
 * academy/partner      a referral partner's page: their links to share, what they earned and were paid
 * Errors from a form show next to that form and clear on the next try. DOM is built with ctx.h and text only.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  if (!P) return;
  let openLesson = "";

  const paragraphs = (h, text) => String(text || "").split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean).map((part) => h("p", { class: "pub-pre", text: part }));
  const https = (url) => /^https:\/\//i.test(String(url || ""));
  const level = (value) => ({ beginner: "Beginner", intermediate: "Intermediate", advanced: "Advanced" })[value] || "All levels";
  const progressBar = (h, percent) => h("div", { class: "pub-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(percent), "aria-label": `${percent}% complete` }, h("span", { style: { width: `${percent}%` } }));
  const starText = (rating) => { const n = Math.max(0, Math.min(5, Math.round(Number(rating) || 0))); return "★".repeat(n) + "☆".repeat(5 - n); };
  const stars = (h, rating) => h("span", { "aria-label": `${rating} out of 5`, style: { color: "#b7791f", letterSpacing: "0.06em" }, text: starText(rating) });
  const wrap = { overflowWrap: "anywhere", wordBreak: "break-word" };

  // A partner's code from the address (?ref=CODE), kept for this visit so it survives a reload, and for this
  // business only: every hosted business shares one web address, so the key is the business's scope from the page.
  const referral = (scope) => {
    let code = "";
    try { code = new URLSearchParams(location.search).get("ref") || ""; } catch { code = ""; }
    code = code.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 30);
    const key = /^[0-9a-f]{8,64}$/.test(String(scope || "")) ? `aira.ref.${scope}` : "";
    try {
      if (key && code) sessionStorage.setItem(key, code);
      else if (key) code = sessionStorage.getItem(key) || "";
    } catch { /* storage unavailable */ }
    return code;
  };

  // A form's own message line: shown next to the form, replaced on every try, cleared on success.
  const messageLine = (h) => {
    const node = h("p", { class: "pub-notice bad", role: "alert", hidden: true });
    return {
      node,
      show(text, tone = "bad") { node.className = `pub-notice ${tone}`; node.setAttribute("role", tone === "bad" ? "alert" : "status"); node.textContent = text; node.hidden = false; },
      clear() { node.hidden = true; node.textContent = ""; },
    };
  };
  // Run a form action: errors land in the form's message line instead of piling up at the top of the page.
  const guarded = (line, fn) => async (event) => {
    line.clear();
    try { await fn(event); } catch (error) { line.show(error && error.message ? error.message : String(error)); }
  };

  // ------------------------------------------------------------------ course page
  P.register("academy", "course", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const course = page.course;
    const ref = referral(page.ref_scope);
    const priceText = course.price_cents ? ctx.money(course.price_cents) : "Free";
    const full = page.seats_left === 0;
    const rating = page.rating || { average: null, count: 0 };
    root.append(
      h("section", { class: "pub-card" },
        https(course.cover_url) ? h("img", { src: course.cover_url, alt: "", style: { width: "100%", maxHeight: "260px", objectFit: "cover", borderRadius: "10px" } }) : null,
        h("span", { class: "pub-badge", text: level(course.level) }),
        h("h1", { text: course.title }),
        course.subtitle ? h("p", { class: "pub-muted", text: course.subtitle }) : null,
        rating.count ? h("p", {}, stars(h, rating.average), h("span", { class: "pub-muted", text: ` ${rating.average} out of 5 · ${rating.count} review${rating.count === 1 ? "" : "s"}` })) : null,
        h("p", { class: "pub-price pub-total", text: priceText }),
        h("p", { class: "pub-muted", text: [`${page.lessons} lesson${page.lessons === 1 ? "" : "s"}`, page.minutes ? `${page.minutes} minutes` : "", page.students ? `${page.students} student${page.students === 1 ? "" : "s"}` : "", page.seats_left !== null && page.seats_left !== undefined ? `${page.seats_left} seat${page.seats_left === 1 ? "" : "s"} left` : ""].filter(Boolean).join(" · ") }),
        ...paragraphs(h, course.description)),
    );
    const curriculum = h("section", { class: "pub-card" }, h("h2", { text: "What you will learn" }));
    page.modules.forEach((module, index) => {
      curriculum.append(h("h3", { text: `${index + 1}. ${module.title}` }));
      const list = h("ul", { style: { margin: "0 0 8px", paddingLeft: "20px", display: "grid", gap: "6px" } });
      for (const lesson of module.lessons) {
        const item = h("li", {}, h("span", { text: lesson.title }), lesson.minutes ? h("span", { class: "pub-muted", text: ` · ${lesson.minutes} min` }) : null, lesson.has_quiz ? h("span", { class: "pub-muted", text: " · quiz" }) : null);
        if (lesson.preview) {
          const body = h("div", { hidden: true, style: { marginTop: "6px" } }, ...paragraphs(h, lesson.body), https(lesson.video_url) ? h("a", { href: lesson.video_url, target: "_blank", rel: "noopener noreferrer", text: "Watch the video" }) : null);
          const toggle = h("button", { type: "button", class: "pub-btn ghost", "aria-expanded": "false", style: { minHeight: "30px", padding: "2px 10px", marginLeft: "8px" }, text: "Free preview", onclick: () => { body.hidden = !body.hidden; toggle.setAttribute("aria-expanded", String(!body.hidden)); } });
          item.append(toggle, body);
        }
        list.append(item);
      }
      curriculum.append(list);
    });
    root.append(curriculum);

    if (page.reviews && page.reviews.length) {
      root.append(h("section", { class: "pub-card" }, h("h2", { text: "What students say" }),
        ...page.reviews.map((review) => h("div", { class: "pub-item" },
          h("div", { style: { display: "flex", justifyContent: "space-between", gap: "8px", flexWrap: "wrap" } }, h("strong", { text: review.name }), stars(h, review.rating)),
          review.comment ? h("p", { class: "pub-pre", style: wrap, text: review.comment }) : null))));
    }

    const name = h("input", { type: "text", autocomplete: "name", required: true, maxlength: 120 });
    const email = h("input", { type: "email", autocomplete: "email", required: true, maxlength: 254 });
    const code = page.requires_code ? h("input", { type: "text", maxlength: 40, autocomplete: "off" }) : null;
    const cohorts = page.cohorts.filter((cohort) => cohort.seats_left !== 0);
    const cohort = cohorts.length ? h("select", {}, h("option", { value: "", text: "Start any time" }), ...cohorts.map((c) => h("option", { value: c.id, text: `${c.name}${c.starts_on ? ` · starts ${ctx.date(c.starts_on)}` : ""}${c.seats_left !== null ? ` · ${c.seats_left} seats` : ""}` }))) : null;
    const line = messageLine(h);
    const result = h("div", { class: "pub-form" });
    const form = h("section", { class: "pub-card pub-form" },
      h("h2", { text: full ? "This course is full" : course.price_cents ? `Enrol for ${priceText}` : "Enrol for free" }),
      full ? h("p", { class: "pub-muted", text: "Ask the instructor about the next intake." }) : null);
    if (!full) {
      const submit = ctx.button(course.price_cents ? `Enrol for ${priceText}` : "Enrol now", guarded(line, async () => {
        if (!name.value.trim()) { name.focus(); throw new Error("Enter your name."); }
        if (!email.value.trim() || !email.checkValidity()) { email.focus(); throw new Error("Enter your email address, like name@example.com."); }
        if (code && !code.value.trim()) { code.focus(); throw new Error("Enter the enrolment code from your instructor."); }
        const reply = await ctx.action("enrol", { name: name.value.trim(), email: email.value.trim(), code: code ? code.value.trim() : "", cohort_id: cohort ? cohort.value : "", ref });
        if (reply.already) { result.replaceChildren(h("p", { class: "pub-notice", role: "status", text: reply.message })); return; }
        const enrolment = reply.enrolment;
        const link = `${location.origin}${reply.student_path}`;
        result.replaceChildren(...[
          h("p", { class: "pub-notice good", role: "status", text: enrolment.status === "active" ? "You're in! Your course is ready." : "You're enrolled. Your lessons unlock as soon as your payment is confirmed." }),
          enrolment.status === "pending_payment" ? ctx.paymentBox(enrolment.amount_due_cents, enrolment.reference) || h("p", { class: "pub-notice", text: `Pay ${ctx.money(enrolment.amount_due_cents)} with reference ${enrolment.reference}.` }) : null,
          h("p", { style: wrap }, h("strong", { text: "Your course page: " }), h("a", { href: reply.student_path, style: wrap, text: link })),
          h("p", { class: "pub-muted", text: "Bookmark it: it is your access. We've emailed it to you too." }),
          h("div", { class: "pub-btn-row" }, h("a", { class: "pub-btn", href: reply.student_path, text: enrolment.status === "active" ? "Start learning" : "Open my course page" })),
        ].filter(Boolean));
        form.querySelectorAll("input, select, button").forEach((node) => { node.disabled = true; });
      }));
      form.append(...[ctx.field("Your name", name), ctx.field("Email", email, "Your course link is sent here."), code ? ctx.field("Enrolment code", code, "From your instructor.") : null, cohort ? ctx.field("Cohort", cohort) : null].filter(Boolean),
        line.node, h("div", { class: "pub-btn-row" }, submit), result);
    }
    root.append(form);
  });

  // ------------------------------------------------------------------ the student's own page
  P.register("academy", "student", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const status = page.student.status;
    const learning = status === "active" || status === "completed";
    root.append(
      h("section", { class: "pub-card" },
        h("span", { class: `pub-badge ${status === "completed" ? "good" : status === "refunded" ? "bad" : ""}`, text: { pending_payment: "Waiting for payment", active: "Learning", completed: "Completed", refunded: "Refunded" }[status] || status }),
        h("h1", { text: page.course.title }),
        h("p", { class: "pub-muted", text: `Welcome, ${page.student.name}.${page.cohort ? ` Cohort: ${page.cohort.name}${page.cohort.starts_on ? `, starting ${ctx.date(page.cohort.starts_on)}` : ""}.` : ""}` }),
        progressBar(h, page.progress.percent),
        h("p", { class: "pub-muted", text: `${page.progress.done} of ${page.progress.total} lessons done${page.progress.quizzes_total ? ` · ${page.progress.quizzes_passed} of ${page.progress.quizzes_total} quizzes passed` : ""}` }),
        page.certificate ? h("div", { class: "pub-btn-row" }, h("a", { class: "pub-btn", href: page.certificate.path, text: "View your certificate" })) : null),
    );
    if (status === "pending_payment") root.append(ctx.paymentBox(page.amount_due_cents, page.student.reference) || h("p", { class: "pub-notice", text: `Pay ${ctx.money(page.amount_due_cents)} with reference ${page.student.reference}; your instructor confirms it.` }));
    if (status === "refunded") root.append(h("p", { class: "pub-notice bad", text: "This enrolment was refunded, so the lessons are closed." }));

    page.modules.forEach((module, mi) => {
      const section = h("section", { class: "pub-card" }, h("h2", { text: `${mi + 1}. ${module.title}` }));
      for (const lesson of module.lessons) section.append(lessonBlock(h, ctx, lesson, learning));
      root.append(section);
    });
    if (page.can_review || page.review) root.append(reviewForm(h, ctx, page.review));
  });

  function reviewForm(h, ctx, existing) {
    let sent = Boolean(existing);
    const picked = { value: existing ? existing.rating : 0 };
    const line = messageLine(h);
    const group = h("div", { role: "radiogroup", "aria-label": "Your rating", style: { display: "flex", gap: "6px", flexWrap: "wrap" } });
    const choices = [1, 2, 3, 4, 5].map((n) => {
      const input = h("input", { type: "radio", name: "ac-rating", value: String(n), "aria-label": `${n} star${n === 1 ? "" : "s"}`, checked: picked.value === n, style: { position: "absolute", opacity: "0", width: "1px", height: "1px" } });
      const label = h("label", { class: "pub-btn ghost", style: { minWidth: "56px", cursor: "pointer", position: "relative" } }, input, h("span", { "aria-hidden": "true", style: { color: "#b7791f" }, text: "★" }), h("span", { text: ` ${n}` }));
      input.addEventListener("change", () => { picked.value = n; paint(); });
      input.addEventListener("focus", () => { label.style.outline = "2px solid currentColor"; });
      input.addEventListener("blur", () => { label.style.outline = ""; });
      group.append(label);
      return { n, label };
    });
    const paint = () => choices.forEach(({ n, label }) => { label.className = `pub-btn ${n <= picked.value ? "secondary" : "ghost"}`; });
    paint();
    const comment = h("textarea", { rows: 3, maxlength: 1500, text: existing ? existing.comment : "" });
    const heading = h("h2", { text: sent ? "Your review" : "Rate this course" });
    const box = h("section", { class: "pub-card pub-form" },
      heading,
      h("p", { class: "pub-muted", text: "Your first name and initial show with your review on the course page, to help others decide." }),
      group,
      ctx.field("What did you think? (optional)", comment),
      line.node);
    const send = ctx.button(sent ? "Update my review" : "Send my review", guarded(line, async () => {
      if (!picked.value) throw new Error("Choose a rating from 1 to 5 stars.");
      await ctx.action("review", { rating: picked.value, comment: comment.value.trim() });
      line.show(sent ? "Thanks, your review is updated." : "Thank you for your review!", "good");
      sent = true;  // from now on sending again updates it
      heading.textContent = "Your review";
      send.textContent = "Update my review";
    }));
    box.append(h("div", { class: "pub-btn-row" }, send));
    return box;
  }

  function lessonBlock(h, ctx, lesson, learning) {
    const header = h("div", { style: { display: "flex", justifyContent: "space-between", gap: "8px", alignItems: "center", flexWrap: "wrap" } },
      h("div", {}, h("strong", { text: lesson.title }), h("div", { class: "pub-muted pub-hint", text: [lesson.minutes ? `${lesson.minutes} min` : "", lesson.has_quiz ? (lesson.quiz_passed ? "quiz passed" : `quiz, pass mark ${lesson.pass_mark}%`) : "", lesson.locked ? "unlocks after payment" : ""].filter(Boolean).join(" · ") })),
      h("span", { class: `pub-badge ${lesson.completed ? "good" : ""}`, text: lesson.completed ? "Done" : lesson.locked ? "Locked" : "To do" }));
    const block = h("div", { class: "pub-item" }, header);
    if (lesson.locked) return block;
    const body = h("div", { class: "pub-form", hidden: openLesson !== lesson.id });
    body.append(...paragraphs(h, lesson.body));
    if (https(lesson.video_url)) body.append(h("a", { class: "pub-btn secondary", href: lesson.video_url, target: "_blank", rel: "noopener noreferrer", text: "Watch the video" }));
    if (learning) {
      if (lesson.questions && lesson.questions.length) body.append(quiz(h, ctx, lesson));
      if (!lesson.completed && !(lesson.questions && lesson.questions.length)) {
        const line = messageLine(h);
        body.append(line.node, h("div", { class: "pub-btn-row" }, ctx.button("Mark as complete", guarded(line, async () => {
          await ctx.action("complete", { lesson_id: lesson.id });
          await ctx.reload();
        }))));
      }
    }
    block.append(h("div", { class: "pub-btn-row" }, h("button", { type: "button", class: "pub-btn ghost", "aria-expanded": String(!body.hidden), text: body.hidden ? "Open lesson" : "Close lesson", onclick: (event) => { body.hidden = !body.hidden; openLesson = body.hidden ? "" : lesson.id; event.currentTarget.textContent = body.hidden ? "Open lesson" : "Close lesson"; event.currentTarget.setAttribute("aria-expanded", String(!body.hidden)); } })), body);
    return block;
  }

  function quiz(h, ctx, lesson) {
    const box = h("div", { class: "pub-card pub-form" }, h("h3", { text: lesson.quiz_passed ? `Quiz (passed${lesson.best_score !== null && lesson.best_score !== undefined ? `, best ${lesson.best_score}%` : ""})` : `Quiz: ${lesson.pass_mark}% to pass` }));
    const groups = lesson.questions.map((question, qi) => {
      const name = `q-${lesson.id}-${qi}`;
      const feedback = h("p", { class: "pub-hint", hidden: true });
      const fieldset = h("fieldset", { style: { border: "1px solid var(--pub-line)", borderRadius: "10px", padding: "10px 12px" } },
        h("legend", { text: `${qi + 1}. ${question.prompt}` }),
        ...question.options.map((option, oi) => h("label", { style: { display: "flex", gap: "8px", alignItems: "center", padding: "4px 0" } }, h("input", { type: "radio", name, value: String(oi) }), h("span", { text: option }))),
        feedback);
      box.append(fieldset);
      return { name, feedback };
    });
    const line = messageLine(h);
    box.append(line.node, h("div", { class: "pub-btn-row" }, ctx.button(lesson.quiz_passed ? "Take it again" : "Submit answers", guarded(line, async () => {
      const answers = groups.map(({ name }) => { const picked = box.querySelector(`input[name="${name}"]:checked`); return picked ? Number(picked.value) : null; });
      if (answers.some((answer) => answer === null)) throw new Error("Answer every question first.");
      const reply = await ctx.action("quiz", { lesson_id: lesson.id, answers });
      reply.results.forEach((item, index) => {
        const feedback = groups[index].feedback;
        feedback.hidden = false;
        feedback.textContent = item.correct ? `Right.${item.explanation ? ` ${item.explanation}` : ""}` : reply.passed ? `The answer was option ${item.answer + 1}.${item.explanation ? ` ${item.explanation}` : ""}` : "Not quite. Read the lesson again and retry.";
        feedback.style.color = item.correct ? "var(--pub-good)" : "var(--pub-bad)";
      });
      line.show(reply.passed ? `You scored ${reply.score}%. Passed!${reply.status === "completed" ? " You completed the course; your certificate is ready." : ""}` : `You scored ${reply.score}%. You need ${reply.pass_mark}% to pass. Try again.`, reply.passed ? "good" : "bad");
      if (reply.passed) setTimeout(() => ctx.reload(), 1800);
    }))));
    return box;
  }

  // ------------------------------------------------------------------ certificate
  P.register("academy", "certificate", (root, data, ctx) => {
    const { h } = ctx;
    const cert = data.page;
    root.append(
      h("section", { class: "pub-card", style: { textAlign: "center", padding: "36px 20px", borderWidth: "3px", borderColor: "var(--pub-accent)" } },
        h("span", { class: "pub-badge good", text: "Verified certificate" }),
        h("p", { class: "pub-muted", style: { textTransform: "uppercase", letterSpacing: "0.12em", margin: "18px 0 0" }, text: "Certificate of completion" }),
        h("p", { class: "pub-muted", text: "This certifies that" }),
        h("h1", { style: { fontSize: "34px", ...wrap }, text: cert.name }),
        h("p", { class: "pub-muted", text: "has completed" }),
        h("h2", { style: { fontSize: "22px" }, text: cert.course }),
        h("p", { class: "pub-muted", text: [cert.lessons ? `${cert.lessons} lessons` : "", cert.minutes ? `${cert.minutes} minutes` : ""].filter(Boolean).join(" · ") }),
        h("p", { text: `Issued by ${cert.issuer} on ${ctx.date(cert.completed_at)}` }),
        h("p", { class: "pub-code", text: cert.code })),
      h("div", { class: "pub-btn-row" }, ctx.button("Print or save as PDF", () => window.print()), ctx.button("Copy link", () => ctx.copy(location.href), "secondary")),
    );
  });

  // ------------------------------------------------------------------ catalog
  const withRef = (path, ref) => (ref ? `${path}?ref=${encodeURIComponent(ref)}` : path);
  const eventWhen = (iso, zone) => {
    try { return new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: zone && zone !== "UTC" ? zone : undefined, timeZoneName: zone && zone !== "UTC" ? "short" : undefined }); } catch { return new Date(iso).toLocaleString(); }
  };

  // An event card's price: only ticket types on sale now count; a free ticket next to paid ones is mentioned.
  const eventPrice = (ctx, event) => {
    if (event.sold_out || event.sales === "sold_out") return "Sold out: join the waitlist";
    if (event.from_cents === null || event.from_cents === undefined) return event.sales === "soon" ? "Tickets soon" : "Not on sale";
    if (!event.from_cents) return "Free";
    return `From ${ctx.money(event.from_cents)}${event.free_option ? " · free tickets too" : ""}`;
  };

  P.register("academy", "catalog", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const ref = referral(page.ref_scope);
    root.append(h("div", {}, h("h1", { text: ctx.business && ctx.business.name ? `${ctx.business.name}: courses and events` : "Courses and events" }), ctx.business && ctx.business.tagline ? h("p", { class: "pub-muted", text: ctx.business.tagline }) : null));
    if (!page.courses.length && !page.events.length) {
      root.append(h("p", { class: "pub-notice", text: "Nothing is open right now. Come back soon." }));
      return;
    }
    if (page.events.length) {
      root.append(h("section", { class: "pub-card" }, h("h2", { text: "Upcoming events" }), h("div", { class: "pub-grid" },
        ...page.events.map((event) => h("a", { class: "pub-item", href: withRef(event.path, ref), style: { textDecoration: "none", color: "inherit", display: "grid", gap: "4px" } },
          https(event.cover_url) ? h("img", { src: event.cover_url, alt: "", style: { width: "100%", maxHeight: "150px", objectFit: "cover", borderRadius: "8px" } }) : null,
          h("strong", { style: wrap, text: event.title }),
          h("span", { class: "pub-muted", text: eventWhen(event.starts_at, page.timezone) }),
          event.venue_name || event.online ? h("span", { class: "pub-muted", text: event.venue_name || "Online" }) : null,
          h("span", { class: `pub-badge ${event.sold_out ? "bad" : ""}`.trim(), text: eventPrice(ctx, event) }))))));
    }
    if (page.courses.length) {
      root.append(h("section", { class: "pub-card" }, h("h2", { text: "Courses" }), h("div", { class: "pub-grid" },
        ...page.courses.map((course) => h("a", { class: "pub-item", href: withRef(course.path, ref), style: { textDecoration: "none", color: "inherit", display: "grid", gap: "4px" } },
          https(course.cover_url) ? h("img", { src: course.cover_url, alt: "", style: { width: "100%", maxHeight: "150px", objectFit: "cover", borderRadius: "8px" } }) : null,
          h("strong", { style: wrap, text: course.title }),
          course.subtitle ? h("span", { class: "pub-muted", text: course.subtitle }) : null,
          h("span", { class: "pub-muted", text: [level(course.level), `${course.lessons} lesson${course.lessons === 1 ? "" : "s"}`, course.minutes ? `${course.minutes} min` : ""].filter(Boolean).join(" · ") }),
          course.rating && course.rating.count ? h("span", {}, stars(h, course.rating.average), h("span", { class: "pub-muted", text: ` ${course.rating.average} (${course.rating.count})` })) : null,
          h("span", { class: `pub-badge ${course.full ? "bad" : ""}`.trim(), text: course.full ? "Full" : course.price_cents ? ctx.money(course.price_cents) : "Free" }))))));
    }
  });

  // ------------------------------------------------------------------ a partner's own page
  P.register("academy", "partner", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const p = page.partner;
    const t = page.totals;
    const STATUS = { pending: "Waiting for their payment", earned: "Earned", paid: "Paid to you", void: "Refunded, no commission" };
    const urlFor = (path) => `${location.origin}${withRef(path, p.code)}`;
    const linkRow = (title, detail, url) => h("div", { class: "pub-item", style: { display: "grid", gap: "6px" } },
      h("strong", { style: wrap, text: title }), detail ? h("span", { class: "pub-muted", text: detail }) : null,
      h("span", { class: "pub-muted", style: { ...wrap, fontSize: "0.86em" }, text: url }),
      h("div", { class: "pub-btn-row" }, ctx.button("Copy link", () => ctx.copy(url), "secondary")));
    root.append(
      h("section", { class: "pub-card" },
        h("span", { class: "pub-badge good", text: "Partner" }),
        h("h1", { text: `Hi ${p.name}` }),
        h("p", { class: "pub-muted", text: `Share these links. When someone enrols or buys tickets through them and pays, you earn ${p.commission_percent}% of what they pay. Your code is ${p.code}.` }),
        h("table", { class: "pub-table" }, h("tbody", {},
          h("tr", {}, h("td", { text: "People you referred" }), h("td", { class: "num", text: String(t.referrals) })),
          h("tr", {}, h("td", { text: "Their purchases" }), h("td", { class: "num", text: ctx.money(t.sales_cents) })),
          h("tr", {}, h("td", { text: "Waiting for their payment" }), h("td", { class: "num", text: ctx.money(t.pending_cents) })),
          h("tr", {}, h("td", { text: "Earned, not paid yet" }), h("td", { class: "num", text: ctx.money(t.owed_cents) })),
          h("tr", {}, h("th", { text: "Paid to you" }), h("th", { class: "num pub-total", text: ctx.money(t.paid_cents) }))))),
    );
    const links = h("section", { class: "pub-card" }, h("h2", { text: "Your links" }));
    if (page.catalog) links.append(linkRow("Everything (courses and events)", "One page with all of it", urlFor(page.catalog.path)));
    for (const item of page.links) links.append(linkRow(item.title, item.type === "course" ? (item.price_cents ? `Course · ${ctx.money(item.price_cents)}` : "Course · free") : `Event · ${eventWhen(item.starts_at, page.timezone)}`, urlFor(item.path)));
    if (!page.catalog && !page.links.length) links.append(h("p", { class: "pub-muted", text: "Nothing is open to share right now. Check back soon." }));
    root.append(links);
    root.append(h("section", { class: "pub-card" }, h("h2", { text: "Your referrals" }),
      page.referrals.length
        ? h("table", { class: "pub-table" }, h("thead", {}, h("tr", {}, h("th", { text: "For" }), h("th", { class: "num", text: "Commission" }))), h("tbody", {},
          ...page.referrals.map((r) => h("tr", {}, h("td", {}, h("span", { style: wrap, text: r.label }), h("div", { class: "pub-muted pub-hint", text: `${ctx.date(r.created_at)} · ${STATUS[r.status] || r.status}` })), h("td", { class: "num", text: ctx.money(r.commission_cents) })))))
        : h("p", { class: "pub-muted", text: "No referrals yet. Share a link above to get started." })));
    if (page.payouts.length) {
      root.append(h("section", { class: "pub-card" }, h("h2", { text: "Payments to you" }), h("table", { class: "pub-table" }, h("tbody", {}, ...page.payouts.map((payout) => h("tr", {}, h("td", { text: ctx.date(payout.created_at) }), h("td", { class: "num", text: ctx.money(payout.amount_cents) })))))));
    }
  });
})();

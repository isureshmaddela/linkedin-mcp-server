/* Aira — Academy: the pages students open without signing in (aira/suite/academy).
 *
 * academy/course       the course page: curriculum, free previews, price and the enrolment form
 * academy/student      a student's own page (their link is their access): lessons, progress, quizzes graded on
 *                      the server (the right options are never sent before a pass), the way to pay, the certificate
 * academy/certificate  a certificate anyone can verify, ready to print
 * DOM is built with ctx.h and text only.
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

  // ------------------------------------------------------------------ course page
  P.register("academy", "course", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const course = page.course;
    const priceText = course.price_cents ? ctx.money(course.price_cents) : "Free";
    const full = page.seats_left === 0;
    root.append(
      h("section", { class: "pub-card" },
        https(course.cover_url) ? h("img", { src: course.cover_url, alt: "", style: { width: "100%", maxHeight: "260px", objectFit: "cover", borderRadius: "10px" } }) : null,
        h("span", { class: "pub-badge", text: level(course.level) }),
        h("h1", { text: course.title }),
        course.subtitle ? h("p", { class: "pub-muted", text: course.subtitle }) : null,
        h("p", { class: "pub-price pub-total", text: priceText }),
        h("p", { class: "pub-muted", text: [`${page.lessons} lessons`, page.minutes ? `${page.minutes} minutes` : "", page.students ? `${page.students} students` : "", page.seats_left !== null && page.seats_left !== undefined ? `${page.seats_left} seats left` : ""].filter(Boolean).join(" · ") }),
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
          const toggle = h("button", { type: "button", class: "pub-btn ghost", style: { minHeight: "30px", padding: "2px 10px", marginLeft: "8px" }, text: "Free preview", onclick: () => { body.hidden = !body.hidden; } });
          item.append(toggle, body);
        }
        list.append(item);
      }
      curriculum.append(list);
    });
    root.append(curriculum);

    const name = h("input", { type: "text", autocomplete: "name", required: true, maxlength: 120 });
    const email = h("input", { type: "email", autocomplete: "email", required: true, maxlength: 254 });
    const code = page.requires_code ? h("input", { type: "text", maxlength: 40, autocomplete: "off" }) : null;
    const cohorts = page.cohorts.filter((cohort) => cohort.seats_left !== 0);
    const cohort = cohorts.length ? h("select", {}, h("option", { value: "", text: "Start any time" }), ...cohorts.map((c) => h("option", { value: c.id, text: `${c.name}${c.starts_on ? ` · starts ${ctx.date(c.starts_on)}` : ""}${c.seats_left !== null ? ` · ${c.seats_left} seats` : ""}` }))) : null;
    const result = h("div", { class: "pub-form" });
    const form = h("section", { class: "pub-card pub-form" },
      h("h2", { text: full ? "This course is full" : course.price_cents ? `Enrol for ${priceText}` : "Enrol for free" }),
      full ? h("p", { class: "pub-muted", text: "Ask the instructor about the next intake." }) : null);
    if (!full) {
      form.append(...[ctx.field("Your name", name), ctx.field("Email", email, "Your course link is sent here."), code ? ctx.field("Enrolment code", code, "From your instructor.") : null, cohort ? ctx.field("Cohort", cohort) : null].filter(Boolean),
        h("div", { class: "pub-btn-row" }, ctx.button(course.price_cents ? `Enrol for ${priceText}` : "Enrol now", async () => {
          if (!name.value.trim() || !email.value.trim()) throw new Error("Enter your name and email.");
          const reply = await ctx.action("enrol", { name: name.value.trim(), email: email.value.trim(), code: code ? code.value.trim() : "", cohort_id: cohort ? cohort.value : "" });
          if (reply.already) { result.replaceChildren(h("p", { class: "pub-notice", text: reply.message })); return; }
          const enrolment = reply.enrolment;
          const link = `${location.origin}${reply.student_path}`;
          result.replaceChildren(...[
            h("p", { class: "pub-notice good", text: enrolment.status === "active" ? "You're in! Your course is ready." : "You're enrolled. Your lessons unlock as soon as your payment is confirmed." }),
            enrolment.status === "pending_payment" ? ctx.paymentBox(enrolment.amount_due_cents, enrolment.reference) || h("p", { class: "pub-notice", text: `Pay ${ctx.money(enrolment.amount_due_cents)} with reference ${enrolment.reference}.` }) : null,
            h("p", {}, h("strong", { text: "Your course page: " }), h("a", { href: reply.student_path, text: link })),
            h("p", { class: "pub-muted", text: "Bookmark it: it is your access. We've emailed it to you too." }),
            h("div", { class: "pub-btn-row" }, h("a", { class: "pub-btn", href: reply.student_path, text: enrolment.status === "active" ? "Start learning" : "Open my course page" })),
          ].filter(Boolean));
          form.querySelectorAll("input, select, button").forEach((node) => { node.disabled = true; });
        })), result);
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
  });

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
        body.append(h("div", { class: "pub-btn-row" }, ctx.button("Mark as complete", async () => {
          const reply = await ctx.action("complete", { lesson_id: lesson.id });
          if (reply.certificate && reply.status === "completed") ctx.notice("You completed the course! Your certificate is ready.", "good");
          await ctx.reload();
        })));
      }
    }
    block.append(h("div", { class: "pub-btn-row" }, h("button", { type: "button", class: "pub-btn ghost", text: body.hidden ? "Open lesson" : "Close lesson", onclick: (event) => { body.hidden = !body.hidden; openLesson = body.hidden ? "" : lesson.id; event.currentTarget.textContent = body.hidden ? "Open lesson" : "Close lesson"; } })), body);
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
    const result = h("p", { hidden: true });
    box.append(h("div", { class: "pub-btn-row" }, ctx.button(lesson.quiz_passed ? "Take it again" : "Submit answers", async () => {
      const answers = groups.map(({ name }) => { const picked = box.querySelector(`input[name="${name}"]:checked`); return picked ? Number(picked.value) : null; });
      if (answers.some((answer) => answer === null)) throw new Error("Answer every question first.");
      const reply = await ctx.action("quiz", { lesson_id: lesson.id, answers });
      reply.results.forEach((item, index) => {
        const feedback = groups[index].feedback;
        feedback.hidden = false;
        feedback.className = `pub-hint ${item.correct ? "" : ""}`;
        feedback.textContent = item.correct ? `Right.${item.explanation ? ` ${item.explanation}` : ""}` : reply.passed ? `The answer was option ${item.answer + 1}.${item.explanation ? ` ${item.explanation}` : ""}` : "Not quite. Read the lesson again and retry.";
        feedback.style.color = item.correct ? "var(--pub-good)" : "var(--pub-bad)";
      });
      result.hidden = false;
      result.className = `pub-notice ${reply.passed ? "good" : "bad"}`;
      result.textContent = reply.passed ? `You scored ${reply.score}%. Passed!${reply.status === "completed" ? " You completed the course; your certificate is ready." : ""}` : `You scored ${reply.score}%. You need ${reply.pass_mark}% to pass. Try again.`;
      if (reply.passed) setTimeout(() => ctx.reload(), 1800);
    })), result);
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
        h("h1", { style: { fontSize: "34px" }, text: cert.name }),
        h("p", { class: "pub-muted", text: "has completed" }),
        h("h2", { style: { fontSize: "22px" }, text: cert.course }),
        h("p", { class: "pub-muted", text: [cert.lessons ? `${cert.lessons} lessons` : "", cert.minutes ? `${cert.minutes} minutes` : ""].filter(Boolean).join(" · ") }),
        h("p", { text: `Issued by ${cert.issuer} on ${ctx.date(cert.completed_at)}` }),
        h("p", { class: "pub-code", text: cert.code })),
      h("div", { class: "pub-btn-row" }, ctx.button("Print or save as PDF", () => window.print()), ctx.button("Copy link", () => ctx.copy(location.href), "secondary")),
    );
  });
})();

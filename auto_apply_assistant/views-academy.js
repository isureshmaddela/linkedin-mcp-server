/* Aira — Academy (#/academy): sell courses and teach.
 *
 * Tabs (?tab=): overview (students, activity, completion, revenue, setup), courses (the list; ?course=<id> opens
 * the editor: details, publishing, the public course page, the curriculum builder with lessons and quizzes, and
 * an outline drafted by the writing model), students (enrolments with progress, payments, links, CSV), cohorts
 * and announcements. Everything runs through /api/academy/* (aira/suite/academy/academy_routes.py).
 * DOM is built with h() and text only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("academy", path, opts);

  const TABS = [
    { id: "overview", label: "Overview" },
    { id: "courses", label: "Courses" },
    { id: "students", label: "Students" },
    { id: "cohorts", label: "Cohorts" },
    { id: "announcements", label: "Announcements" },
  ];
  const LEVELS = [["all", "All levels"], ["beginner", "Beginner"], ["intermediate", "Intermediate"], ["advanced", "Advanced"]];
  const STUDENT_STATUSES = [["", "Any status"], ["pending_payment", "Waiting for payment"], ["active", "Learning"], ["completed", "Completed"], ["refunded", "Refunded"]];
  const TONES = { pending_payment: "warn", active: "info", completed: "good", refunded: "bad", published: "good", draft: "", archived: "" };
  const go = (query) => Aira.router.go(["academy"], query);

  const courseFields = [
    { name: "title", label: "Title", required: true, placeholder: "Python for beginners" },
    { name: "subtitle", label: "Subtitle", placeholder: "Write real programs in four weeks" },
    { name: "description", label: "Description", type: "textarea", rows: 5, hint: "What students will learn and who it is for." },
    { name: "level", label: "Level", type: "select", options: LEVELS },
    { name: "price_cents", label: "Price", type: "money", hint: "0 for a free course." },
    { name: "cover_url", label: "Cover image link", type: "url", placeholder: "https://.../cover.jpg" },
    { name: "enrol_code", label: "Enrolment code", hint: "Optional. Students must type it to enrol (for a class or a partner)." },
    { name: "capacity", label: "Seats", type: "number", min: 1, hint: "Optional. Leave empty for no limit." },
  ];

  async function coursesList() {
    return (await api("/courses")).courses;
  }

  // ------------------------------------------------------------------ overview
  async function overview(body) {
    const data = await api("/overview");
    const money = (cents) => S.money(cents, data.currency);
    const steps = data.checklist || [];
    const remind = data.inactive ? S.button(`Remind ${data.inactive} inactive student${data.inactive === 1 ? "" : "s"}`, async () => {
      const result = await api("/nudges", { method: "POST", body: {} });
      Aira.toast(`${result.queued} reminders are waiting in the outbox (Business hub).`, "success");
    }, { kind: "secondary" }) : null;
    body.append(
      S.kpis([
        { label: "Students", value: data.students, hint: `${data.completed} completed` },
        { label: "Active this week", value: data.active_this_week },
        { label: "Completion rate", value: `${data.completion_rate}%`, tone: data.completion_rate >= 50 ? "good" : "" },
        { label: "Revenue this month", value: money(data.revenue_this_month_cents), hint: `${money(data.revenue_total_cents)} all time`, tone: "good" },
        { label: "Waiting for payment", value: data.pending_payments, tone: data.pending_payments ? "warn" : "" },
      ]),
      S.grid(
        steps.some((step) => !step.done) ? S.section("Get started", S.checklist(steps)) : null,
        S.section("Top courses", h("div", { class: "btn-row" }, remind, S.button("New course", () => newCourse(), { kind: "secondary sm" })),
          S.table({
            columns: [
              { key: "title", label: "Course" },
              { key: "status", label: "Status", render: (row) => S.pill(row.status, TONES) },
              { key: "students", label: "Students", num: true },
              { key: "completed", label: "Completed", num: true, render: (row) => row.completed || 0 },
            ],
            rows: data.top_courses,
            empty: Aira.ui.empty("No courses yet", "Create a course, or let Aira draft an outline from a topic.", S.button("Create a course", () => newCourse())),
            onRow: (row) => go({ tab: "courses", course: row.id }),
          })),
        S.section("Recent activity", S.activity(data.activity)),
      ),
    );
  }

  // ------------------------------------------------------------------ courses
  async function newCourse(values = {}) {
    const created = await S.edit({ title: "New course", fields: courseFields, values: { level: "all", price_cents: 0, ...values }, submitLabel: "Create course", onSave: async (form) => (await api("/courses", { method: "POST", body: clean(form) })).course });
    if (created) go({ tab: "courses", course: created.id });
  }

  function clean(values) {
    const out = { ...values };
    if (out.capacity === null || out.capacity === "" || Number.isNaN(out.capacity)) out.capacity = null;
    if (out.price_cents === null) out.price_cents = 0;
    return out;
  }

  async function outlineWizard() {
    const form = S.form([
      { name: "topic", label: "Topic", required: true, placeholder: "Digital marketing for small shops" },
      { name: "level", label: "Level", type: "select", options: LEVELS },
      { name: "price_cents", label: "Price", type: "money", hint: "0 for free" },
    ], { level: "all", price_cents: 0 });
    const preview = h("div", { class: "suite-body" });
    let outline = null;
    Aira.modal({
      title: "Draft a course with AI",
      wide: true,
      body: h("div", { class: "suite-body" }, h("p", { class: "muted", text: "Aira drafts modules and lessons from your topic. You edit everything before students see it." }), form.node, preview),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Draft outline", kind: "secondary", onClick: async () => {
          const values = form.read();
          if (!values.topic) { form.setError("Enter a topic."); return false; }
          form.setError("");
          try { outline = await api("/outline-draft", { method: "POST", body: { topic: values.topic, level: values.level } }); } catch (error) { form.setError(error.message); return false; }
          preview.replaceChildren(
            h("p", { class: "muted small", text: outline.source === "model" ? "Drafted by the writing model." : "A starting template (no writing model is running). Edit it to fit." }),
            ...outline.modules.map((module) => h("div", {}, h("strong", { text: module.title }), h("ul", {}, ...module.lessons.map((lesson) => h("li", { text: lesson.title }))))),
          );
          return false;
        } },
        { label: "Create course", kind: "primary", onClick: async () => {
          const values = form.read();
          if (!values.topic) { form.setError("Enter a topic."); return false; }
          try {
            if (!outline) outline = await api("/outline-draft", { method: "POST", body: { topic: values.topic, level: values.level } });
            const course = (await api("/courses", { method: "POST", body: { title: values.topic, level: values.level, price_cents: values.price_cents || 0 } })).course;
            await api(`/courses/${course.id}/outline`, { method: "POST", body: { modules: outline.modules } });
            go({ tab: "courses", course: course.id });
            return true;
          } catch (error) { form.setError(error.message); return false; }
        } },
      ],
    });
  }

  async function courses(body, route) {
    if (route.query.course) return courseEditor(body, route.query.course);
    const rows = await coursesList();
    body.append(
      h("div", { class: "btn-row" }, S.button("New course", () => newCourse()), S.button("Draft with AI", () => outlineWizard(), { kind: "secondary" })),
      S.table({
        columns: [
          { key: "title", label: "Course", render: (row) => h("div", {}, h("strong", { text: row.title }), row.subtitle ? h("div", { class: "muted small", text: row.subtitle }) : null) },
          { key: "status", label: "Status", render: (row) => S.pill(row.status, TONES) },
          { key: "price_cents", label: "Price", num: true, render: (row) => (row.price_cents ? S.money(row.price_cents) : "Free") },
          { key: "lessons", label: "Lessons", num: true },
          { key: "students", label: "Students", num: true },
          { key: "pending", label: "Unpaid", num: true },
          { key: "link", label: "Page", render: (row) => (row.link ? h("a", { href: S.absolute(row.link.path), target: "_blank", rel: "noopener noreferrer", text: "Open" }) : "") },
        ],
        rows,
        empty: Aira.ui.empty("No courses yet", "Create your first course: a title, a price and a few lessons is enough to start.", S.button("Create a course", () => newCourse())),
        onRow: (row) => go({ tab: "courses", course: row.id }),
      }),
    );
  }

  async function courseEditor(body, courseId) {
    const data = await api(`/courses/${courseId}`);
    const course = data.course;
    const reload = () => go({ tab: "courses", course: courseId, t: Date.now() });
    const setStatus = (status) => async () => { await api(`/courses/${courseId}/status`, { method: "POST", body: { status } }); reload(); };
    const actions = h("div", { class: "btn-row" },
      h("a", { class: "btn ghost sm", href: "#/academy?tab=courses", text: "All courses" }),
      S.button("Edit details", async () => { if (await S.edit({ title: "Course details", fields: courseFields, values: course, onSave: (values) => api(`/courses/${courseId}`, { method: "PATCH", body: clean(values) }) })) reload(); }, { kind: "secondary sm" }),
      course.status !== "published" ? S.button("Publish", setStatus("published"), { kind: "sm" }) : S.button("Unpublish", setStatus("draft"), { kind: "secondary sm" }),
      course.status !== "archived" ? S.button("Archive", setStatus("archived"), { kind: "ghost sm" }) : null,
      S.button("Delete", async () => { if (await S.confirmDelete(`“${course.title}”`)) { await api(`/courses/${courseId}`, { method: "DELETE" }); go({ tab: "courses" }); } }, { kind: "danger secondary sm" }),
    );
    const share = course.status === "published"
      ? S.share({
        title: "Course page",
        text: "Students read the curriculum, try the free previews and enrol here.",
        link: data.link,
        onCreate: async () => (await api(`/courses/${courseId}/link`, { method: "POST", body: {} })).link,
        onRevoke: () => api(`/courses/${courseId}/link`, { method: "DELETE" }),
      })
      : h("p", { class: "muted", text: "Publish the course to get a page students can enrol from." });
    body.append(
      h("div", {}, h("h2", { class: "suite-h2", text: course.title }), h("div", { class: "btn-row" }, S.pill(course.status, TONES), h("span", { class: "muted", text: course.price_cents ? S.money(course.price_cents) : "Free" }), course.enrol_code ? h("span", { class: "muted", text: `Code: ${course.enrol_code}` }) : null)),
      actions,
      S.kpis([
        { label: "Lessons", value: data.stats.lessons, hint: `${data.stats.minutes} minutes` },
        { label: "Quizzes", value: data.stats.quizzes },
        { label: "Students", value: data.stats.students, hint: course.capacity ? `${data.stats.seats_taken} of ${course.capacity} seats` : "" },
      ]),
      share,
      curriculumBuilder(courseId, data.modules, reload),
    );
  }

  function curriculumBuilder(courseId, modules, reload) {
    const order = async (moduleIds, lessonMap) => { await api(`/courses/${courseId}/reorder`, { method: "POST", body: { modules: moduleIds, lessons: lessonMap } }); reload(); };
    const moduleIds = modules.map((module) => module.id);
    const lessonMap = () => Object.fromEntries(modules.map((module) => [module.id, module.lessons.map((lesson) => lesson.id)]));
    const swap = (list, i, j) => { const copy = [...list]; [copy[i], copy[j]] = [copy[j], copy[i]]; return copy; };
    const lessonFields = [
      { name: "title", label: "Title", required: true },
      { name: "body", label: "Lesson", type: "textarea", rows: 10, hint: "Plain text. Blank lines start a new paragraph." },
      { name: "video_url", label: "Video link", type: "url", placeholder: "https://youtube.com/..." },
      { name: "minutes", label: "Minutes", type: "number", min: 0 },
      { name: "preview", label: "Free preview (anyone can read it on the course page)", type: "checkbox" },
    ];
    const blocks = modules.map((module, mi) => {
      const lessons = module.lessons.map((lesson, li) => h("li", { class: "ac-lesson" },
        h("div", { class: "ac-lesson-main" }, h("strong", { text: `${li + 1}. ${lesson.title}` }), h("span", { class: "muted small", text: [lesson.minutes ? `${lesson.minutes} min` : "", lesson.preview ? "free preview" : "", lesson.question_count ? `quiz: ${lesson.question_count} questions, pass ${lesson.pass_mark}%` : "", lesson.video_url ? "video" : ""].filter(Boolean).join(" · ") })),
        h("div", { class: "btn-row" },
          li > 0 ? S.button("↑", () => { const map = lessonMap(); map[module.id] = swap(map[module.id], li, li - 1); return order(null, map); }, { kind: "ghost sm", title: "Move up" }) : null,
          li < module.lessons.length - 1 ? S.button("↓", () => { const map = lessonMap(); map[module.id] = swap(map[module.id], li, li + 1); return order(null, map); }, { kind: "ghost sm", title: "Move down" }) : null,
          S.button("Edit", async () => { if (await S.edit({ title: "Lesson", wide: true, fields: lessonFields, values: lesson, onSave: (values) => api(`/lessons/${lesson.id}`, { method: "PATCH", body: values }) })) reload(); }, { kind: "secondary sm" }),
          S.button(lesson.question_count ? "Quiz" : "Add quiz", () => quizEditor(lesson, reload), { kind: "secondary sm" }),
          S.button("Delete", async () => { if (await S.confirmDelete(`the lesson “${lesson.title}”`)) { await api(`/lessons/${lesson.id}`, { method: "DELETE" }); reload(); } }, { kind: "ghost sm" }))));
      return h("div", { class: "card ac-module" },
        h("div", { class: "ac-module-head" }, h("strong", { text: `Module ${mi + 1}: ${module.title}` }), h("div", { class: "btn-row" },
          mi > 0 ? S.button("↑", () => order(swap(moduleIds, mi, mi - 1), null), { kind: "ghost sm", title: "Move up" }) : null,
          mi < modules.length - 1 ? S.button("↓", () => order(swap(moduleIds, mi, mi + 1), null), { kind: "ghost sm", title: "Move down" }) : null,
          S.button("Rename", async () => { if (await S.edit({ title: "Module", fields: [{ name: "title", label: "Title", required: true }], values: module, onSave: (values) => api(`/modules/${module.id}`, { method: "PATCH", body: values }) })) reload(); }, { kind: "ghost sm" }),
          S.button("Delete", async () => { if (await S.confirmDelete(`the module “${module.title}” and its lessons`)) { await api(`/modules/${module.id}`, { method: "DELETE" }); reload(); } }, { kind: "ghost sm" }))),
        lessons.length ? h("ol", { class: "ac-lessons" }, ...lessons) : h("p", { class: "muted", text: "No lessons in this module yet." }),
        h("div", { class: "btn-row" }, S.button("Add lesson", async () => { if (await S.edit({ title: "New lesson", wide: true, fields: lessonFields, values: { minutes: 10 }, submitLabel: "Add lesson", onSave: (values) => api(`/modules/${module.id}/lessons`, { method: "POST", body: values }) })) reload(); }, { kind: "secondary sm" })));
    });
    return S.section("Curriculum",
      modules.length ? null : Aira.ui.empty("No modules yet", "Add a module, then lessons inside it. A free-preview lesson helps students decide to enrol."),
      ...blocks,
      h("div", { class: "btn-row" }, S.button("Add module", async () => { if (await S.edit({ title: "New module", fields: [{ name: "title", label: "Title", required: true }], submitLabel: "Add module", onSave: (values) => api(`/courses/${courseId}/modules`, { method: "POST", body: values }) })) reload(); }, { kind: "secondary" })),
    );
  }

  function quizEditor(lesson, reload) {
    let questions = (lesson.questions || []).map((q) => ({ ...q, options: [...q.options] }));
    const pass = h("input", { type: "number", min: 1, max: 100, value: String(lesson.pass_mark || 70) });
    const list = h("div", { class: "suite-body" });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const render = () => {
      list.replaceChildren(...questions.map((question, qi) => {
        const prompt = h("input", { type: "text", value: question.prompt, oninput: (e) => { question.prompt = e.target.value; } });
        const options = h("textarea", { rows: 4, text: question.options.join("\n"), oninput: (e) => { question.options = e.target.value.split("\n"); } });
        const correct = h("input", { type: "number", min: 1, max: 6, value: String((question.correct || 0) + 1), oninput: (e) => { question.correct = Number(e.target.value) - 1; } });
        const explanation = h("input", { type: "text", value: question.explanation || "", oninput: (e) => { question.explanation = e.target.value; } });
        return h("div", { class: "card suite-form" }, h("strong", { text: `Question ${qi + 1}` }),
          Aira.ui.field("Question", prompt), Aira.ui.field("Options (one per line, 2 to 6)", options), Aira.ui.field("Right option (number)", correct), Aira.ui.field("Explanation (shown after passing)", explanation),
          h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn ghost sm", text: "Remove", onclick: () => { questions.splice(qi, 1); render(); } })));
      }));
      if (!questions.length) list.append(h("p", { class: "muted", text: "No questions. Add some, or draft them from the lesson text." }));
    };
    render();
    Aira.modal({
      title: `Quiz: ${lesson.title}`,
      wide: true,
      body: h("div", { class: "suite-body" }, h("p", { class: "muted", text: "Graded on the server: students never see the right options until they pass." }), Aira.ui.field("Pass mark (%)", pass), list,
        h("div", { class: "btn-row" },
          h("button", { type: "button", class: "btn secondary sm", text: "Add question", onclick: () => { questions.push({ prompt: "", options: ["", ""], correct: 0, explanation: "" }); render(); } }),
          S.button("Draft from the lesson", async () => { const draft = await api(`/lessons/${lesson.id}/quiz-draft`, { method: "POST", body: {} }); questions = draft.questions.map((q) => ({ ...q, options: [...q.options] })); render(); Aira.toast(draft.source === "model" ? "Drafted. Check every answer." : "A template from the lesson text. Edit it.", ""); }, { kind: "secondary sm" })),
        error),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Save quiz", kind: "primary", onClick: async () => {
          error.hidden = true;
          const payload = questions.map((q) => ({ prompt: q.prompt, options: q.options.map((o) => o.trim()).filter(Boolean), correct: q.correct, explanation: q.explanation }));
          try { await api(`/lessons/${lesson.id}/quiz`, { method: "POST", body: { questions: payload, pass_mark: Number(pass.value) || 70 } }); reload(); return true; } catch (err) { error.textContent = err.message; error.hidden = false; return false; }
        } },
      ],
    });
  }

  // ------------------------------------------------------------------ students
  async function students(body, route) {
    const courseList = await coursesList();
    const query = { course_id: route.query.course_id || "", status: route.query.status || "", q: route.query.q || "" };
    const params = new URLSearchParams(Object.entries(query).filter(([, v]) => v)).toString();
    const rows = (await api(`/students${params ? `?${params}` : ""}`)).students;
    const courseSelect = Aira.ui.select([["", "All courses"], ...courseList.map((c) => [c.id, c.title])], query.course_id, { "aria-label": "Course" });
    const statusSelect = Aira.ui.select(STUDENT_STATUSES, query.status, { "aria-label": "Status" });
    const search = h("input", { type: "search", placeholder: "Name, email or reference", value: query.q, "aria-label": "Search", "data-search": "1" });
    const apply = () => go({ tab: "students", course_id: courseSelect.value, status: statusSelect.value, q: search.value.trim() });
    courseSelect.addEventListener("change", apply);
    statusSelect.addEventListener("change", apply);
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") apply(); });
    body.append(
      h("div", { class: "btn-row" }, courseSelect, statusSelect, search,
        S.button("Enrol a student", () => enrolStudent(courseList, query.course_id)),
        S.button("Download CSV", () => Aira.download(`/api/academy/students?format=csv${params ? `&${params}` : ""}`, "students.csv"), { kind: "secondary" })),
      S.table({
        columns: [
          { key: "name", label: "Student", render: (row) => h("div", {}, h("strong", { text: row.name }), h("div", { class: "muted small", text: row.email })) },
          { key: "course_title", label: "Course", render: (row) => h("div", {}, h("span", { text: row.course_title }), row.cohort_name ? h("div", { class: "muted small", text: row.cohort_name }) : null) },
          { key: "status", label: "Status", render: (row) => S.pill(row.status, TONES) },
          { key: "progress", label: "Progress", num: true, render: (row) => `${row.progress}%` },
          { key: "last_activity_at", label: "Last active", render: (row) => (row.last_activity_at ? Aira.fmt.ago(row.last_activity_at) : "") },
        ],
        rows,
        empty: Aira.ui.empty("No students yet", courseList.length ? "Share a course page, or enrol someone yourself." : "Create and publish a course first."),
        onRow: (row) => studentDrawer(row.id, () => go({ ...query, tab: "students", t: Date.now() })),
      }),
    );
  }

  async function enrolStudent(courseList, courseId) {
    if (!courseList.length) { Aira.toast("Create a course first."); return; }
    const done = await S.edit({
      title: "Enrol a student",
      fields: [
        { name: "course_id", label: "Course", type: "select", options: courseList.filter((c) => c.status !== "archived").map((c) => [c.id, c.title]) },
        { name: "name", label: "Name", required: true },
        { name: "email", label: "Email", type: "email", required: true },
        { name: "paid", label: "Already paid (records the payment for a paid course)", type: "checkbox" },
        { name: "notes", label: "Notes", type: "textarea", rows: 2 },
      ],
      values: { course_id: courseId || (courseList[0] && courseList[0].id) },
      submitLabel: "Enrol",
      onSave: (values) => api("/students", { method: "POST", body: values }),
    });
    if (done) { Aira.toast("Enrolled. Their welcome email is in the outbox.", "success"); go({ tab: "students", t: Date.now() }); }
  }

  async function studentDrawer(id, refresh) {
    const data = await api(`/students/${id}`);
    const s = data.student;
    const reopen = () => { Aira.closeDrawer(); refresh(); studentDrawer(id, refresh); };
    const cohortSelect = data.cohorts.length ? Aira.ui.select([["", "No cohort"], ...data.cohorts.map((c) => [c.id, c.name])], s.cohort_id || "", { "aria-label": "Cohort" }) : null;
    if (cohortSelect) cohortSelect.addEventListener("change", async () => { try { await api(`/students/${id}`, { method: "PATCH", body: { cohort_id: cohortSelect.value } }); Aira.toast("Cohort saved.", "success"); } catch { cohortSelect.value = s.cohort_id || ""; } });
    const actions = h("div", { class: "btn-row" },
      s.status === "pending_payment" ? S.button(`Mark paid (${S.money(s.price_cents)})`, async () => { await api(`/students/${id}/paid`, { method: "POST", body: {} }); Aira.toast("Payment recorded; lessons unlocked.", "success"); reopen(); }) : null,
      s.paid_cents && s.status !== "refunded" ? S.button("Refund", async () => { if (await Aira.confirm({ title: "Refund", message: `Mark ${S.money(s.paid_cents)} as refunded? Their lessons lock and the payment stops counting.`, confirmLabel: "Refund", danger: true })) { await api(`/students/${id}/refund`, { method: "POST", body: {} }); reopen(); } }, { kind: "danger secondary" }) : null,
      S.button("Email their link again", async () => { await api(`/students/${id}/resend`, { method: "POST", body: {} }); Aira.toast("Queued in the outbox.", "success"); }, { kind: "secondary" }));
    Aira.drawer({
      title: s.name,
      subtitle: `${s.email} · ${data.course.title}`,
      body: h("div", { class: "suite-body" },
        h("div", { class: "btn-row" }, S.pill(s.status, TONES), h("span", { class: "muted", text: `Reference ${s.reference}` })),
        h("div", { class: "pub-progress ac-progress", role: "progressbar", "aria-valuenow": String(data.progress.percent), "aria-valuemin": "0", "aria-valuemax": "100" }, h("span", { style: { width: `${data.progress.percent}%`, display: "block", height: "100%", background: "var(--brand)" } })),
        Aira.ui.kv([["Progress", `${data.progress.done} of ${data.progress.total} lessons (${data.progress.percent}%)`], ["Quizzes passed", `${data.progress.quizzes_passed} of ${data.progress.quizzes_total}`], ["Enrolled", S.when(s.created_at)], ["Last active", S.when(s.last_activity_at)], ["Paid", s.paid_cents ? S.money(s.paid_cents) : ""], ["Certificate", s.certificate_code], ["Notes", s.notes]]),
        cohortSelect ? Aira.ui.field("Cohort", cohortSelect) : null,
        actions,
        S.share({ title: "Student's own page", text: "Their access to the lessons. Only share it with this student.", link: data.link, onCreate: async () => (await api(`/students/${id}/link`, { method: "POST", body: {} })).link, onRevoke: () => api(`/students/${id}/link`, { method: "DELETE" }) }),
        data.certificate ? S.share({ title: "Certificate", text: "", link: data.certificate }) : null,
        data.attempts.length ? S.section("Quiz attempts", S.table({ columns: [{ key: "lesson_title", label: "Lesson" }, { key: "score", label: "Score", num: true, render: (a) => `${a.score}%` }, { key: "passed", label: "", render: (a) => S.pill(a.passed ? "passed" : "not passed", { passed: "good", "not passed": "warn" }) }, { key: "at", label: "When", render: (a) => Aira.fmt.ago(a.at) }], rows: data.attempts })) : null),
    });
  }

  // ------------------------------------------------------------------ cohorts
  async function cohorts(body) {
    const [courseList, rows] = await Promise.all([coursesList(), api("/cohorts").then((d) => d.cohorts)]);
    const fields = [
      { name: "course_id", label: "Course", type: "select", options: courseList.map((c) => [c.id, c.title]) },
      { name: "name", label: "Name", required: true, placeholder: "November evening batch" },
      { name: "starts_on", label: "Starts on", type: "date" },
      { name: "capacity", label: "Seats", type: "number", min: 1, hint: "Optional." },
    ];
    const reload = () => go({ tab: "cohorts", t: Date.now() });
    body.append(
      h("p", { class: "muted", text: "Cohorts group students who start together: a batch, a class or a live programme. Open cohorts show on the course page." }),
      h("div", { class: "btn-row" }, S.button("New cohort", async () => { if (!courseList.length) { Aira.toast("Create a course first."); return; } if (await S.edit({ title: "New cohort", fields, values: { course_id: courseList[0].id }, submitLabel: "Create", onSave: (values) => api("/cohorts", { method: "POST", body: clean(values) }) })) reload(); })),
      S.table({
        columns: [
          { key: "name", label: "Cohort" },
          { key: "course_title", label: "Course" },
          { key: "starts_on", label: "Starts", render: (row) => S.day(row.starts_on) },
          { key: "students", label: "Students", num: true, render: (row) => (row.capacity ? `${row.students} / ${row.capacity}` : row.students) },
          { key: "id", label: "", render: (row) => S.button("Delete", async () => { if (await S.confirmDelete(`the cohort “${row.name}” (students stay enrolled)`)) { await api(`/cohorts/${row.id}`, { method: "DELETE" }); reload(); } }, { kind: "ghost sm" }) },
        ],
        rows,
        empty: "No cohorts yet",
        onRow: async (row) => { if (await S.edit({ title: "Cohort", fields: fields.slice(1), values: row, onSave: (values) => api(`/cohorts/${row.id}`, { method: "PATCH", body: clean(values) }) })) reload(); },
      }),
    );
  }

  // ------------------------------------------------------------------ announcements
  async function announcements(body) {
    const [courseList, rows, cohortRows] = await Promise.all([coursesList(), api("/announcements").then((d) => d.announcements), api("/cohorts").then((d) => d.cohorts)]);
    body.append(
      h("p", { class: "muted", text: "Tell a course's students about a live session, a new lesson or an offer. Messages wait in the Business hub outbox for your approval." }),
      h("div", { class: "btn-row" }, S.button("New announcement", async () => {
        if (!courseList.length) { Aira.toast("Create a course first."); return; }
        const sent = await S.edit({
          title: "New announcement",
          wide: true,
          fields: [
            { name: "course_id", label: "Course", type: "select", options: courseList.map((c) => [c.id, c.title]) },
            { name: "cohort_id", label: "Only this cohort", type: "select", options: [["", "Everyone in the course"], ...cohortRows.map((c) => [c.id, `${c.name} (${c.course_title})`])] },
            { name: "subject", label: "Subject", required: true },
            { name: "body", label: "Message", type: "textarea", rows: 8, required: true },
          ],
          values: { course_id: courseList[0].id },
          submitLabel: "Queue for students",
          onSave: (values) => api("/announcements", { method: "POST", body: values }),
        });
        if (sent) { Aira.toast(`Queued for ${sent.announcement.recipients} students.`, "success"); go({ tab: "announcements", t: Date.now() }); }
      })),
      S.table({
        columns: [
          { key: "created_at", label: "When", render: (row) => S.when(row.created_at) },
          { key: "course_title", label: "Course" },
          { key: "subject", label: "Subject" },
          { key: "recipients", label: "Students", num: true },
        ],
        rows,
        empty: "No announcements yet",
        onRow: (row) => Aira.modal({ title: row.subject, body: h("pre", { class: "suite-pre", text: row.body }), actions: [{ label: "Close", kind: "secondary" }] }),
      }),
    );
  }

  Aira.views.academy = async (main, route) => {
    await S.business();
    const body = S.page(main, {
      title: "Academy",
      subtitle: "Sell courses, teach and keep students moving.",
      actions: [S.button("New course", () => newCourse(), { kind: "secondary" })],
    });
    const tab = S.tabs(body, route, "academy", TABS);
    const panel = h("div", { class: "suite-body" });
    body.append(panel, h("style", { text: ".ac-lessons{list-style:none;margin:0;padding:0;display:grid;gap:6px}.ac-lesson{display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap;padding:8px 10px;border:1px solid var(--line);border-radius:8px}.ac-lesson-main{display:grid;gap:2px;min-width:0}.ac-module{display:grid;gap:10px}.ac-module-head{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center}.ac-progress{height:8px;border-radius:999px;background:var(--surface-2);overflow:hidden}" }));
    const render = async () => {
      panel.replaceChildren(Aira.ui.skeleton(4));
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "courses") await courses(holder, route);
        else if (tab === "students") await students(holder, route);
        else if (tab === "cohorts") await cohorts(holder);
        else if (tab === "announcements") await announcements(holder);
        else await overview(holder);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
  };
})();

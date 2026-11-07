/* Aira — Life stages (#/journey?tab=stages): one life from before birth to remembrance.
 *
 * Whose life: your own, or (adults) someone you look after: a child, even one on the way, an older parent, a
 * partner (?life=<id>; none means your own). The page shows:
 * - the stage of life now (from the birth date), its age range, what matters in it and how much of it is done;
 * - the road of every stage: pick one (?stage=<id>) to look back at it or ahead to it;
 * - the stage's checklist by area of life (health, mind, family, money, purpose, papers, legacy), with what is
 *   still open from earlier stages: tick it, set it aside ("not for me"), or turn it into a goal, a habit, a document
 *   or a record; and its milestones (your own go on your timeline);
 * - ideas for each chosen interest at this age (to learn, to do, careers, ways to earn with Monosage's apps, ways to
 *   give back), and a plan for the stage from Monosage's writing helper;
 * - the life book: growth, vaccines and check-ups (with the next one due, reminded on Today), schools, work, homes;
 * - wishes and legacy (your own life, adults); a life that ended is kept as a remembrance.
 * Everything runs through /api/journey/lives, /records and /wishes (aira/suite/journey/lifebook.py). The forms for
 * goals, habits and documents are the Life app's own (Aira.journeyKit, from views-journey.js).
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const K = () => Aira.journeyKit;
  const api = (path, opts) => S.api("journey", path, opts);
  const put = (node, ...children) => node.append(...children.filter(Boolean));

  const STAGE_MARK = { pregnancy: "🤰", baby: "👶", toddler: "🧸", preschool: "🎈", child: "🎒", teen: "🎧", young_adult: "🎓", building: "🏡", midlife: "🧭", late_career: "🌅", retirement: "🌳", elder: "🕊️", remembered: "🕯️" };
  const AREA_MARK = { health: "🩺", mind: "🧠", heart: "❤️", money: "💰", purpose: "🧭", papers: "📄", legacy: "🌳" };
  const RELATIONS = [["child", "My child"], ["grandchild", "My grandchild"], ["partner", "My partner"], ["parent", "My parent"], ["grandparent", "My grandparent"], ["sibling", "My brother or sister"], ["friend", "A friend"], ["other", "Someone else"]];
  const RELATION = Object.fromEntries(RELATIONS);
  const RELATION_MARK = { self: "🙂", child: "🧒", grandchild: "👶", partner: "💞", parent: "🧓", grandparent: "👵", sibling: "🧑", friend: "🤝", other: "🙂" };
  const IDEA_KINDS = { learn: "Learn", do: "Do", career: "Career", earn: "Earn", give: "Give back" };
  const APPS = { sites: "Sites", experiences: "Courses & Events", commerce: "Commerce", studio: "Sales", videos: "Videos", play: "Play", stocks: "Stocks", care: "Health", careers: "Careers" };
  const STATUS = { done: ["Done", "good"], planned: ["Planned", "info"], skipped: ["Not for me", ""] };
  const ORGAN = [["", "Not said"], ["yes", "Yes, I want to be a donor"], ["no", "No"], ["undecided", "Not decided yet"]];
  const BLOOD = [["", "Not known"], ...["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"].map((group) => [group, group])];
  const TOO_YOUNG = new Set(["pregnancy", "baby", "toddler"]); // interests show ideas from about three
  const RECORDS = {
    growth: { title: "Growth", add: "Add a measurement", mark: "📏" },
    vaccine: { title: "Vaccines", add: "Add a vaccine", mark: "💉" },
    checkup: { title: "Check-ups and screenings", add: "Add a check-up", mark: "🩺" },
    education: { title: "Schools and courses", add: "Add a school or course", mark: "🎓" },
    work: { title: "Work", add: "Add work", mark: "💼" },
    home: { title: "Homes", add: "Add a home", mark: "🏠" },
  };

  const plural = (count, one, many) => K().plural(count, one, many);
  const today = () => K().localDay();

  // ---------------------------------------------------------------- words for ages
  function ageText(life) {
    if (life.passed) return [life.birth_date ? S.day(life.birth_date) : "", S.day(life.died_on)].filter(Boolean).join(" – ");
    if (life.expected) return `Due ${S.day(life.birth_date)}`;
    if (life.approximate || life.age_months === null || life.age_months === undefined) return "";
    const months = life.age_months;
    if (months < 1) return life.age_days === 1 ? "1 day old" : `${life.age_days} days old`;
    if (months < 24) return months === 1 ? "1 month old" : `${months} months old`;
    return `${Math.floor(months / 12)} years old`;
  }
  function inMonths(months) {
    if (months <= 1) return "next month";
    if (months < 24) return `in ${months} months`;
    const years = Math.round(months / 12);
    return years === 1 ? "in a year" : `in ${years} years`;
  }
  // Months between two ISO dates (a baby's age at a measurement).
  function monthsAt(birth, day) {
    if (!birth || !day) return null;
    const [by, bm, bd] = birth.split("-").map(Number);
    const [y, m, d] = day.split("-").map(Number);
    return (y - by) * 12 + (m - bm) - (d < bd ? 1 : 0);
  }
  function ageAt(birth, day) {
    const months = monthsAt(birth, day);
    if (months === null || months < 0) return "";
    return months < 24 ? `${months} mo` : `${Math.floor(months / 12)} y`;
  }

  // ---------------------------------------------------------------- forms
  function detailsForm(life, onSaved) {
    const own = life && life.self;
    const fields = [
      own ? null : { name: "name", label: "Name", required: true, placeholder: "Mira" },
      own ? null : { name: "relation", label: "Who they are to you", type: "select", options: RELATIONS },
      { name: "birth_date", label: own ? "Your birth date" : "Birth date, or the day the baby is due", type: "date", hint: own ? "Your stage of life comes from it. It is also your birthday in the family's list." : "Add a baby before they are born: Life stages follows the pregnancy." },
      { name: "birth_time", label: "Time of birth (optional)", placeholder: "06:45" },
      { name: "birth_place", label: "Place of birth (optional)", placeholder: "Hyderabad" },
      { name: "blood_group", label: "Blood group", type: "select", options: BLOOD },
      { name: "notes", label: "Notes (optional)", type: "textarea", rows: 2, hint: "Allergies, conditions or anything worth knowing." },
      life ? null : { name: "add_to_timeline", label: "Put their birth on my timeline", type: "checkbox" },
    ].filter(Boolean);
    return S.edit({
      title: life ? (own ? "Your details" : `${life.name}: details`) : "Someone you look after",
      fields,
      values: life ? { ...life } : { relation: "child", add_to_timeline: true },
      submitLabel: life ? "Save" : "Add",
      onSave: async (values) => {
        const body = { ...values };
        if (life) delete body.add_to_timeline;
        const result = await api(life ? `/lives/${life.id}` : "/lives", { method: life ? "PATCH" : "POST", body });
        Aira.toast(life ? "Saved." : `${result.life.name} was added. Their life book starts here.`, "success");
        await onSaved(result.life);
      },
    });
  }

  function passedForm(view) {
    const life = view.life;
    return S.edit({
      title: `Record ${life.name}'s passing`,
      fields: [
        { name: "died_on", label: "The day they died", type: "date", required: true },
        { name: "died_place", label: "Where (optional)" },
        { name: "died_note", label: "A few words to remember them (optional)", type: "textarea", rows: 3 },
        { name: "add_to_timeline", label: "Remember them on my timeline", type: "checkbox" },
      ],
      values: { died_on: today(), add_to_timeline: true },
      onSave: async (values) => {
        await api(`/lives/${life.id}/passed`, { method: "POST", body: values });
        Aira.toast("We are sorry for your loss. Their life book is kept as a remembrance.", "info");
        view.rerender();
      },
    });
  }

  function milestoneForm(view, stage, title) {
    const life = view.life;
    return S.edit({
      title: title ? `${title}` : "Record a moment",
      fields: [
        { name: "title", label: "What happened", required: true, placeholder: "First steps" },
        { name: "day", label: "When", type: "date", required: true },
        life.self ? { name: "location", label: "Where (optional)" } : null,
        { name: "notes", label: "The story (optional)", type: "textarea", rows: 3 },
      ].filter(Boolean),
      values: { title, day: today() },
      submitLabel: "Save the moment",
      onSave: async (values) => {
        await api(`/lives/${life.id}/milestones`, { method: "POST", body: { ...values, stage: stage ? stage.id : "" } });
        Aira.toast(life.self ? "Added to your timeline." : `Added to ${life.name}'s life book.`, "success");
        view.rerender();
      },
    });
  }

  function recordFields(kind, life) {
    const notes = { name: "notes", label: "Notes (optional)", type: "textarea", rows: 2 };
    if (kind === "growth") {
      const baby = life.age_months !== null && life.age_months < 36;
      return [
        { name: "day", label: "Measured on", type: "date", required: true },
        { name: "height_cm", label: "Height (cm)", type: "number", step: 0.1, min: 0, placeholder: "65.5" },
        { name: "weight_kg", label: "Weight (kg)", type: "number", step: 0.1, min: 0, placeholder: "7.2" },
        baby ? { name: "head_cm", label: "Head size (cm)", type: "number", step: 0.1, min: 0 } : null,
        notes,
      ].filter(Boolean);
    }
    const due = { name: "next_due", label: kind === "vaccine" ? "Next dose due (optional)" : "Next one due (optional)", type: "date", hint: "Monosage reminds you on Today a month before." };
    return {
      vaccine: [{ name: "title", label: "Vaccine", required: true, placeholder: "MMR, polio, flu" }, { name: "detail", label: "Dose (optional)", placeholder: "First dose" }, { name: "day", label: "Given on", type: "date", required: true }, due, notes],
      checkup: [{ name: "title", label: "Check-up or screening", required: true, placeholder: "Eye test, dentist, blood pressure" }, { name: "detail", label: "Result (optional)", placeholder: "All clear" }, { name: "day", label: "Date", type: "date", required: true }, due, notes],
      education: [{ name: "title", label: "School, college or course", required: true }, { name: "detail", label: "What was studied, or the qualification (optional)" }, { name: "day", label: "Started", type: "date" }, { name: "end_day", label: "Finished", type: "date" }, notes],
      work: [{ name: "title", label: "Where", required: true, placeholder: "The company or organization" }, { name: "detail", label: "Role (optional)" }, { name: "day", label: "Started", type: "date" }, { name: "end_day", label: "Left", type: "date" }, notes],
      home: [{ name: "title", label: "Town or city", required: true }, { name: "detail", label: "Address (optional)" }, { name: "day", label: "Moved in", type: "date" }, { name: "end_day", label: "Moved out", type: "date" }, notes],
    }[kind];
  }
  function recordForm(view, kind, record) {
    const meta = RECORDS[kind];
    return S.edit({
      title: record ? `Edit: ${meta.title.toLowerCase()}` : meta.add,
      fields: recordFields(kind, view.life),
      values: record ? { ...record } : { day: ["education", "work", "home"].includes(kind) ? "" : today() },
      onSave: async (values) => {
        await api(record ? `/records/${record.id}` : `/lives/${view.life.id}/records`, { method: record ? "PATCH" : "POST", body: record ? values : { kind, ...values } });
        Aira.toast("Saved in the life book.", "success");
        view.rerender();
      },
    });
  }

  function wishesForm(view) {
    const wishes = view.data.wishes;
    return S.edit({
      title: "Your wishes",
      wide: true,
      fields: [
        ...wishes.fields.map((field) => ({ name: field.id, label: field.title, type: "textarea", rows: 3, hint: field.hint })),
        { name: "organ_donation", label: "Organ donation", type: "select", options: ORGAN },
      ],
      values: wishes.values,
      submitLabel: "Save my wishes",
      onSave: async (values) => {
        await api("/wishes", { method: "PUT", body: values });
        Aira.toast("Your wishes are saved. Tell someone you trust where to find them.", "success");
        view.rerender();
      },
    });
  }

  async function setItem(view, id, body) {
    await api(`/lives/${view.life.id}/items/${id}`, { method: "PUT", body });
    view.rerender();
  }

  // A suggestion becomes a goal in Life; the item remembers it, and is done when the goal is reached.
  function makeGoal(view, item, money = false) {
    const life = view.life;
    return K().newGoal(view.rerender, {
      title: life.self ? item.title : `${life.name}: ${item.title}`,
      category: item.goal_area || "personal",
      description: item.description,
      ...(money ? { measure: "money", category: "money" } : {}),
      after: item.id ? (goal) => api(`/lives/${life.id}/items/${item.id}`, { method: "PUT", body: { status: "planned", goal_id: goal.id } }) : null,
    });
  }

  function planModal(view) {
    const life = view.life;
    const holder = h("div", { class: "jr-plan", "aria-live": "polite" }, h("p", { class: "muted", text: "Thinking about this stage of life..." }));
    Aira.modal({ title: life.self ? "Your plan for this stage" : `A plan for ${life.name}`, wide: true, body: holder, actions: [{ label: "Close", kind: "primary" }] });
    api(`/lives/${life.id}/plan`, { method: "POST", body: {} }).then((plan) => {
      holder.replaceChildren(
        plan.summary ? h("p", { class: "jr-plan-summary", text: plan.summary }) : null,
        h("ol", { class: "jr-plan-steps" }, ...plan.steps.map((step) => h("li", { class: "jr-plan-step" },
          h("span", { class: "jr-mark", "aria-hidden": "true", text: AREA_MARK[step.area] || "•" }),
          h("div", { class: "jr-grow" }, h("strong", { text: step.title }), step.description ? h("p", { class: "muted small", text: step.description }) : null),
          S.button("Make it a goal", () => makeGoal(view, { title: step.title, description: step.description, goal_area: { health: "health", mind: "learning", heart: "family", money: "money", purpose: "career", papers: "personal", legacy: "family" }[step.area] || "personal" }), { kind: "secondary sm" })))),
        h("p", { class: "muted small", text: plan.source === "model" ? "Written by Monosage's writing helper for this stage of life. Use what fits." : "From the checklist for this stage and the chosen interests. Connect a writing model for a plan in your own words." }));
      if (!plan.steps.length) holder.append(h("p", { class: "muted", text: "Everything on this stage's list is done or set aside. Well done." }));
    }).catch((error) => holder.replaceChildren(Aira.ui.error(error, () => planModal(view))));
  }

  // ---------------------------------------------------------------- pieces of the page
  function people(view) {
    const { list, life } = view;
    if (list.lives.length < 2 && !list.may_keep_others) return null;
    return h("div", { class: "jr-people", role: "group", "aria-label": "Whose life" },
      ...list.lives.map((person) => {
        const on = person.id === life.id;
        return h("button", { type: "button", class: `jr-person ${on ? "on" : ""} ${person.passed ? "passed" : ""}`, "aria-pressed": String(on), onclick: () => view.go({ life: person.self ? "" : person.id, stage: "" }) },
          h("span", { class: "jr-person-mark", "aria-hidden": "true", text: person.passed ? "🕯️" : person.self ? RELATION_MARK.self : RELATION_MARK[person.relation] || "🙂" }),
          h("span", { class: "jr-person-text" }, h("span", { class: "jr-person-name", text: person.self ? `${person.name} (you)` : person.name }), h("span", { class: "muted small", text: person.stage.title })));
      }),
      list.may_keep_others ? S.button("＋ Someone you look after", () => addLife(view.rerender), { kind: "ghost sm" }) : null);
  }

  function birthPrompt(view) {
    const input = h("input", { type: "date", "aria-label": "Your birth date", max: today() });
    const save = S.button("Save", async () => {
      if (!input.value) { input.focus(); return; }
      await api(`/lives/${view.life.id}`, { method: "PATCH", body: { birth_date: input.value } });
      Aira.toast("Saved. Here is your stage of life.", "success");
      view.rerender();
    }, { kind: "primary sm" });
    return h("div", { class: "jr-birth-prompt" }, h("label", { class: "jr-grow" }, h("span", { class: "small", text: "When were you born? Your stage below is a guess until you say." }), input), save);
  }

  function hero(view) {
    const { life, data } = view;
    const stage = life.stage;
    const total = data.current.total + data.current.carried.length;
    const done = data.current.done;
    const who = life.self ? "You" : `${life.name} · ${RELATION[life.relation] || ""}`;
    const actions = [
      S.button("Edit details", () => detailsForm(life, () => view.rerender()), { kind: "secondary sm" }),
      life.passed ? null : S.button("Ask for a plan", () => planModal(view), { kind: "primary sm" }),
      S.button("Download life book", () => Aira.download(`/api/journey/export/lives/${life.id}`, "life-book.csv"), { kind: "ghost sm" }),
    ];
    if (!life.self && data.may_keep_others) {
      actions.push(life.passed
        ? S.button("Undo the date of death", async () => { if (await Aira.confirm({ title: "Undo the date of death", message: `Was ${life.name}'s passing recorded by mistake? Their book becomes a living one again.`, confirmLabel: "Undo" })) { await api(`/lives/${life.id}/passed`, { method: "DELETE" }); view.rerender(); } }, { kind: "ghost sm" })
        : (life.expected ? null : S.button("Record their passing", () => passedForm(view), { kind: "ghost sm" })));
      actions.push(S.button("Remove", async () => { if (await S.confirmDelete(`${life.name}'s life book`)) { await api(`/lives/${life.id}`, { method: "DELETE" }); view.go({ life: "", stage: "" }); } }, { kind: "ghost sm" }));
    }
    return h("section", { class: `card jr-stage-hero ${life.passed ? "passed" : ""}`, "aria-label": "Stage of life" },
      h("div", { class: "jr-stage-mark", "aria-hidden": "true", text: STAGE_MARK[stage.id] || "🌱" }),
      h("div", { class: "jr-grow jr-stage-text" },
        h("p", { class: "muted small", text: [who, ageText(life)].filter(Boolean).join(" · ") }),
        h("h2", { class: "jr-stage-title", text: stage.title }),
        h("p", { class: "jr-stage-ages", text: stage.label }),
        h("p", { text: stage.description }),
        life.passed ? h("p", { class: "jr-memorial", text: `In loving memory${life.died_place ? ` · ${life.died_place}` : ""}` }) : null,
        life.passed && life.died_note ? h("blockquote", { class: "jr-memorial-note", text: life.died_note }) : null,
        total ? h("div", { class: "jr-stage-progress" }, K().progressBar(Math.round((done / total) * 100), "This stage"), h("span", { class: "small", text: `${done} of ${total} done in this stage` })) : null,
        life.next_stage ? h("p", { class: "muted small", text: `Next: ${life.next_stage.title} (${life.next_stage.label}), ${inMonths(life.next_stage.in_months)}` }) : null,
        life.self && life.approximate ? birthPrompt(view) : null,
        h("div", { class: "btn-row" }, ...actions.filter(Boolean))));
  }

  function road(view, selected) {
    const list = h("ol", { class: "jr-road", "aria-label": "The stages of life" }, ...view.data.road.map((stage) => {
      const on = stage.id === selected;
      const note = stage.state === "current" ? (view.life.passed ? "" : view.life.self ? "You are here" : "Now")
        : stage.state === "past" ? (stage.moments.length ? plural(stage.moments.length, "moment", "moments") : "Looking back")
          : stage.starts_on ? `From ${stage.starts_on.slice(0, 4)}` : "";
      return h("li", {}, h("button", { type: "button", class: `jr-road-stop ${stage.state} ${on ? "on" : ""}`, "aria-pressed": String(on), onclick: () => view.go({ stage: stage.state === "current" ? "" : stage.id }) },
        h("span", { class: "jr-road-mark", "aria-hidden": "true", text: STAGE_MARK[stage.id] || "•" }),
        h("span", { class: "jr-road-title", text: stage.title }),
        h("span", { class: "muted small", text: stage.label }),
        note ? h("span", { class: "jr-road-note small", text: note }) : null));
    }));
    requestAnimationFrame(() => { const on = list.querySelector(".jr-road-stop.on"); if (on && list.scrollWidth > list.clientWidth) list.scrollLeft = on.parentElement.offsetLeft - list.clientWidth / 2 + on.clientWidth / 2; });
    return list;
  }

  function primaryAction(view, item) {
    const life = view.life;
    const [what, kind] = item.do.split(":");
    if (what === "goal") return S.button("Make it a goal", () => makeGoal(view, item, kind === "money"), { kind: "secondary sm" });
    if (what === "habit") return S.button("Make it a habit", () => K().habitForm(null, view.rerender, item.title, () => api(`/lives/${life.id}/items/${item.id}`, { method: "PUT", body: { status: "planned" } })), { kind: "secondary sm" });
    if (what === "document") return S.button("Add the document", () => K().documentForm(null, () => setItem(view, item.id, { status: "done" }), { kind }), { kind: "secondary sm" });
    if (what === "record" && kind === "milestone") return S.button("Record a memory", () => milestoneForm(view, null, ""), { kind: "secondary sm" });
    if (what === "record" && RECORDS[kind]) return S.button(RECORDS[kind].add, () => recordForm(view, kind, null), { kind: "secondary sm" });
    if (what === "wishes" && life.self && view.data.wishes) return S.button("Write your wishes", () => wishesForm(view), { kind: "secondary sm" });
    if (what === "interests") return S.button("Choose interests", () => { const box = document.querySelector(".jr-interests"); if (box) { box.scrollIntoView({ block: "center" }); const first = box.querySelector("button"); if (first) first.focus({ preventScroll: true }); } }, { kind: "secondary sm" });
    if (what === "app" && APPS[kind]) return h("a", { class: "btn secondary sm", href: `#/${kind}`, text: `Open ${APPS[kind]}` });
    return null;
  }

  function itemRow(view, item, { actions = true, carried = false } = {}) {
    const done = item.status === "done";
    const toggle = h("button", { type: "button", class: `life-check ${done ? "done" : ""}`, "aria-pressed": String(done), "aria-label": `${done ? "Not done" : "Done"}: ${item.title}`, disabled: !actions },
      h("span", { "aria-hidden": "true", text: done ? "✓" : "" }), h("span", { class: "life-check-label", text: done ? "Done" : "Done?" }));
    toggle.addEventListener("click", () => Aira.withBusy(toggle, () => setItem(view, item.id, { status: done ? "open" : "done" })));
    const [label, tone] = STATUS[item.status] || ["", ""];
    return h("li", { class: `jr-item ${item.status}`, dataset: { jrOpen: `item:${item.id}` } },
      actions ? toggle : null,
      h("div", { class: "jr-grow" },
        h("div", { class: "jr-item-head" }, h("strong", { class: "jr-item-title", text: item.title }), label && !done ? h("span", { class: `suite-pill as-written ${tone}`, text: label }) : null),
        h("p", { class: "muted small", text: item.description }),
        carried ? h("p", { class: "muted small", text: `From ${item.stage_title}` }) : null,
        item.goal ? h("a", { class: "small", href: `#/journey?tab=goals&status=all&goal=${item.goal.id}`, text: `Goal: ${item.goal.title} · ${item.goal.progress}%` }) : null,
        actions && !done ? h("div", { class: "btn-row" },
          item.status === "skipped" ? null : primaryAction(view, item),
          S.button(item.status === "skipped" ? "Put it back" : "Not for me", () => setItem(view, item.id, { status: item.status === "skipped" ? "open" : "skipped" }), { kind: "ghost sm" })) : null));
  }

  function milestones(view, stage) {
    const ahead = stage.state === "future";
    const named = new Set(stage.milestones.map((m) => m.title.toLowerCase()));
    const others = stage.moments.filter((m) => !named.has(m.title.toLowerCase()));
    if (!stage.milestones.length && !others.length && ahead) return null;
    return h("div", { class: "jr-area-group" },
      h("h3", { class: "jr-area-title" }, h("span", { "aria-hidden": "true", text: "⭐ " }), h("span", { text: view.life.self ? "Milestones (they go on your timeline)" : "Milestones" })),
      stage.milestones.length ? h("div", { class: "jr-milestones" }, ...stage.milestones.map((m) => (m.recorded
        ? h("span", { class: "jr-milestone done" }, h("span", { "aria-hidden": "true", text: "✓ " }), h("span", { text: `${m.title} · ${S.day(m.recorded.day)}` }))
        : ahead ? h("span", { class: "jr-milestone ahead", text: m.title })
          : h("button", { type: "button", class: "jr-milestone", onclick: () => milestoneForm(view, stage, m.title) }, h("span", { "aria-hidden": "true", text: "＋ " }), h("span", { text: m.title }))))) : null,
      others.length ? h("ul", { class: "life-lines" }, ...others.map((m) => h("li", { class: "life-line" },
        h("span", { class: "jr-mark", "aria-hidden": "true", text: m.type === "event" ? (K().KIND_MARK[m.kind] || "📅") : "⭐" }),
        h("div", {}, h("span", { class: "life-line-text", text: m.title }), h("span", { class: "muted life-line-sub", text: S.day(m.day) }))))) : null,
      ahead || view.life.passed ? null : S.button("Record another moment", () => milestoneForm(view, stage, ""), { kind: "ghost sm" }));
  }

  function stagePanel(view, stage) {
    const { data, life } = view;
    const current = stage.state === "current";
    const heading = current ? (life.passed ? "For the people who loved them" : "What matters now") : stage.state === "past" ? `Looking back: ${stage.title}` : `Coming up: ${stage.title}`;
    const actions = stage.state !== "future";
    const groups = data.areas.map((area) => [area, stage.items.filter((item) => item.area === area.id)]).filter(([, items]) => items.length);
    const list = h("div", { class: "jr-stage-items" }, ...groups.map(([area, items]) => h("div", { class: "jr-area-group" },
      h("h3", { class: "jr-area-title" }, h("span", { "aria-hidden": "true", text: `${AREA_MARK[area.id]} ` }), h("span", { text: area.title })),
      h("ul", { class: "jr-items" }, ...items.map((item) => itemRow(view, item, { actions }))))));
    const carried = current && data.current.carried.length ? h("div", { class: "jr-area-group jr-carried" },
      h("h3", { class: "jr-area-title", text: "Still to do from earlier stages" }),
      h("ul", { class: "jr-items" }, ...data.current.carried.map((item) => itemRow(view, item, { carried: true })))) : null;
    // Looking back, the moments come first and the old checklist folds away.
    const past = stage.state === "past";
    return S.section(heading,
      current ? null : h("p", { class: "muted", text: `${stage.label}${stage.starts_on && !past ? ` · from ${S.day(stage.starts_on)}` : ""}. ${stage.description}` }),
      past ? milestones(view, stage) : null,
      past ? h("details", { class: "jr-then" }, h("summary", { text: `What mattered then (${stage.items.length})` }), list) : list,
      carried,
      past ? null : milestones(view, stage));
  }

  function ideasSection(view, stageId) {
    const { data, life } = view;
    if (life.passed) return null;
    const chosen = new Set(life.interests);
    const chips = h("div", { class: "jr-interests", role: "group", "aria-label": "Interests" }, ...data.interests.map((interest) => {
      const on = chosen.has(interest.id);
      const chip = h("button", { type: "button", class: `jr-interest ${on ? "on" : ""}`, "aria-pressed": String(on) }, h("span", { "aria-hidden": "true", text: `${interest.mark} ` }), h("span", { text: interest.title }));
      chip.addEventListener("click", () => Aira.withBusy(chip, async () => {
        const next = on ? life.interests.filter((id) => id !== interest.id) : [...life.interests, interest.id];
        await api(`/lives/${life.id}`, { method: "PATCH", body: { interests: next } });
        view.rerender();
      }));
      return chip;
    }));
    const young = TOO_YOUNG.has(stageId);
    const cards = data.ideas.length ? h("div", { class: "jr-cards jr-idea-cards" }, ...data.ideas.map((idea) => {
      const done = idea.status === "done";
      return h("article", { class: `card jr-card jr-idea ${idea.status}`, dataset: { jrOpen: `item:${idea.id}` } },
        h("div", { class: "jr-card-head" }, h("span", { class: "muted small", text: `${idea.mark} ${idea.interest_title}` }), h("span", { class: `suite-pill as-written ${idea.kind === "earn" ? "good" : "info"}`, text: IDEA_KINDS[idea.kind] || idea.kind })),
        h("h3", { text: idea.title }),
        h("p", { class: "muted small", text: idea.description }),
        idea.goal ? h("a", { class: "small", href: `#/journey?tab=goals&status=all&goal=${idea.goal.id}`, text: `Goal: ${idea.goal.title} · ${idea.goal.progress}%` }) : null,
        h("div", { class: "btn-row" },
          done || idea.status === "skipped" ? h("span", { class: "suite-pill as-written", text: done ? "Done" : "Not for me" }) : S.button("Make it a goal", () => makeGoal(view, { ...idea, goal_area: idea.goal_area }), { kind: "secondary sm" }),
          idea.app && APPS[idea.app] ? h("a", { class: "btn ghost sm", href: `#/${idea.app}`, text: `Open ${APPS[idea.app]}` }) : null,
          done || idea.status === "skipped" ? S.button("Undo", () => setItem(view, idea.id, { status: "open" }), { kind: "ghost sm" })
            : S.button("Done", () => setItem(view, idea.id, { status: "done" }), { kind: "ghost sm" }),
          done || idea.status === "skipped" ? null : S.button("Not for me", () => setItem(view, idea.id, { status: "skipped" }), { kind: "ghost sm" })));
    })) : h("p", { class: "muted", text: young ? "Ideas for interests start at about three. Until then the best thing is play, talk, songs and stories together. You can already note what they enjoy." : chosen.size ? "Nothing new for these interests at this stage." : "Choose what you love, and see ideas that fit this stage of life: things to learn and do, careers, ways to earn and ways to give back." });
    return S.section(life.self ? "Guided by your interests" : `Guided by ${life.name}'s interests`,
      chips, cards,
      data.ideas.length ? h("div", { class: "btn-row" }, S.button("Ask for a plan", () => planModal(view), { kind: "secondary sm" })) : null);
  }

  function growthChart(records, birth) {
    const points = records.filter((r) => r.weight_kg || r.height_cm).slice().reverse();
    if (points.length < 2) return null;
    const draw = (key, label) => {
      const values = points.filter((p) => p[key]);
      if (values.length < 2) return null;
      const min = Math.min(...values.map((p) => p[key]));
      const max = Math.max(...values.map((p) => p[key]));
      const span = max - min || 1;
      const path = values.map((p, i) => `${i ? "L" : "M"}${(i / (values.length - 1)) * 200} ${56 - ((p[key] - min) / span) * 48}`).join(" ");
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "-4 0 208 60");
      svg.setAttribute("class", "jr-spark");
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", `${label}: from ${values[0][key]} to ${values[values.length - 1][key]}`);
      const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
      line.setAttribute("d", path);
      svg.append(line);
      return h("figure", { class: "jr-spark-box" }, svg, h("figcaption", { class: "muted small", text: `${label}: ${values[0][key]} → ${values[values.length - 1][key]} (${ageAt(birth, values[0].day) || S.day(values[0].day)} to ${ageAt(birth, values[values.length - 1].day) || S.day(values[values.length - 1].day)})` }));
    };
    return h("div", { class: "jr-sparks" }, draw("height_cm", "Height in cm"), draw("weight_kg", "Weight in kg"));
  }

  function recordList(view, kind) {
    const life = view.life;
    const rows = view.data.records[kind] || [];
    const edit = (record) => h("div", { class: "btn-row" },
      S.button("Edit", () => recordForm(view, kind, record), { kind: "ghost sm" }),
      S.button("Delete", async () => { if (await S.confirmDelete(`"${record.title}"`)) { await api(`/records/${record.id}`, { method: "DELETE" }); view.rerender(); } }, { kind: "ghost sm" }));
    if (!rows.length) return null;
    if (kind === "growth") {
      return h("div", {}, growthChart(rows, life.birth_date),
        h("div", { class: "table-wrap" }, h("table", { class: "table jr-growth" },
          h("thead", {}, h("tr", {}, ...["Date", "Age", "Height", "Weight", "Head", ""].map((label) => h("th", { text: label })))),
          h("tbody", {}, ...rows.map((r) => h("tr", {},
            h("td", { text: S.day(r.day) }), h("td", { text: ageAt(life.birth_date, r.day) }),
            h("td", { class: "num", text: r.height_cm ? `${r.height_cm} cm` : "" }), h("td", { class: "num", text: r.weight_kg ? `${r.weight_kg} kg` : "" }), h("td", { class: "num", text: r.head_cm ? `${r.head_cm} cm` : "" }),
            h("td", {}, edit(r))))))));
    }
    const span = (r) => (["education", "work", "home"].includes(kind)
      ? [r.day ? r.day.slice(0, 4) : "", r.end_day ? r.end_day.slice(0, 4) : r.day ? "now" : ""].filter(Boolean).join(" – ")
      : S.day(r.day));
    return h("ul", { class: "life-lines" }, ...rows.map((r) => h("li", { class: "life-line jr-record" },
      h("div", { class: "jr-grow" },
        h("span", { class: "life-line-text", text: [r.title, r.detail].filter(Boolean).join(" · ") }),
        h("span", { class: "muted life-line-sub", text: [span(r), r.next_due ? `next due ${S.day(r.next_due)}` : "", r.notes].filter(Boolean).join(" · ") })),
      edit(r))));
  }

  function recordsSection(view) {
    const { data, life } = view;
    const months = life.age_months === null ? 0 : life.age_months;
    const has = (kind) => (data.records[kind] || []).length > 0;
    const kinds = Object.keys(RECORDS).filter((kind) => {
      if (life.expected) return kind === "checkup" || has(kind);
      if (kind === "growth") return months < 228 || has(kind);
      if (kind === "education") return months >= 30 || has(kind);
      if (kind === "work") return months >= 156 || has(kind);
      return true;
    });
    const dueList = data.due.length ? h("ul", { class: "life-lines jr-due" }, ...data.due.map((item) => h("li", { class: `life-line ${item.days_left < 0 ? "late" : ""}` },
      h("span", { class: "jr-mark", "aria-hidden": "true", text: RECORDS[item.kind].mark }),
      h("div", {}, h("span", { class: "life-line-text", text: [item.title, item.detail].filter(Boolean).join(" · ") }), h("span", { class: "muted life-line-sub", text: `${item.days_left < 0 ? "Was due" : "Due"} ${S.day(item.next_due)} · ${K().inDays(item.days_left)}` }))))) : null;
    return S.section(life.self ? "Your life book" : `${life.name}'s life book`,
      h("p", { class: "muted small" }, h("span", { text: "Everything worth keeping, from the first measurement onwards. Medicines, readings and an emergency card are in the " }), h("a", { href: "#/care", text: "Health app" }), h("span", { text: "." })),
      dueList ? h("div", { class: "jr-area-group" }, h("h3", { class: "jr-area-title", text: "Coming up" }), dueList) : null,
      h("div", { class: "jr-book" }, ...kinds.map((kind) => h("div", { class: "jr-area-group" },
        h("div", { class: "jr-book-head" }, h("h3", { class: "jr-area-title" }, h("span", { "aria-hidden": "true", text: `${RECORDS[kind].mark} ` }), h("span", { text: RECORDS[kind].title })), S.button(RECORDS[kind].add, () => recordForm(view, kind, null), { kind: "ghost sm" })),
        recordList(view, kind) || h("p", { class: "muted small", text: "Nothing yet." })))));
  }

  function wishesSection(view) {
    const wishes = view.data.wishes;
    return S.section("Wishes and legacy",
      h("p", { class: "muted", text: "Nobody likes to think about it, but writing down your wishes is one of the kindest things you can do for the people you love. Only you can see this." }),
      h("div", { class: "jr-stage-progress" }, K().progressBar(Math.round((wishes.filled / wishes.total) * 100), "Wishes written"), h("span", { class: "small", text: `${wishes.filled} of ${wishes.total} written${wishes.updated_at ? ` · last changed ${S.day(wishes.updated_at.slice(0, 10))}` : ""}` })),
      h("ul", { class: "life-lines" }, ...wishes.fields.map((field) => h("li", { class: "life-line" },
        h("span", { class: "jr-mark", "aria-hidden": "true", text: wishes.values[field.id] ? "✓" : "○" }),
        h("div", { class: "jr-grow" }, h("span", { class: "life-line-text", text: field.title }), h("span", { class: "muted life-line-sub jr-wish-text", text: wishes.values[field.id] || "Not written yet" }))))),
      h("div", { class: "btn-row" },
        S.button(wishes.filled ? "Change your wishes" : "Write your wishes", () => wishesForm(view)),
        S.button("Download to print", () => Aira.download("/api/journey/export/wishes", "my-wishes.txt"), { kind: "ghost" })),
      h("p", { class: "muted small", text: "Print it, sign it, and tell someone you trust where it is. In many places a will or an advance directive needs witnesses to count: ask a lawyer or your local office." }));
  }

  // ---------------------------------------------------------------- the tab
  async function tab(body, route, rerender) {
    const q = route.query;
    const lifeId = /^[0-9a-f]{32}$/.test(q.life || "") ? q.life : "";
    const [list, data] = await Promise.all([api("/lives"), api(lifeId ? `/lives/${lifeId}` : "/lives/me")]);
    const go = (changes) => Aira.router.go(["journey"], { ...q, tab: "stages", ...changes });
    const view = { data, life: data.life, list, rerender, go };
    const current = data.road.find((stage) => stage.state === "current");
    const stage = data.road.find((s) => s.id === q.stage) || current;
    put(body,
      people(view),
      hero(view),
      road(view, stage.id),
      stagePanel(view, stage),
      stage === current ? ideasSection(view, stage.id) : null,
      recordsSection(view),
      data.life.self && data.wishes ? wishesSection(view) : null);
  }

  async function addLife(rerender) {
    await detailsForm(null, (made) => {
      Aira.router.go(["journey"], { tab: "stages", life: made.id });
      rerender();
    });
  }

  // "+ New" > Record a milestone: a moment of your own life, on your timeline.
  async function milestone(rerender) {
    const own = (await api("/lives")).lives.find((person) => person.self);
    await milestoneForm({ life: own, rerender }, own.stage, "");
  }

  // The overview's card: your stage of life, how much of it is done, and the next few things.
  function overviewCard(summary) {
    const life = summary.life;
    const pct = summary.total ? Math.round((summary.done / summary.total) * 100) : 0;
    return S.section("Your stage of life",
      h("a", { class: "jr-stage-mini", href: "#/journey?tab=stages" },
        h("span", { class: "jr-stage-mark sm", "aria-hidden": "true", text: STAGE_MARK[life.stage.id] || "🌱" }),
        h("span", { class: "jr-grow jr-stage-mini-text" },
          h("strong", { text: `${life.stage.title} · ${life.stage.label}` }),
          h("span", { class: "muted small", text: life.approximate ? "Add your birth date for your exact stage" : ageText(life) }),
          K().progressBar(pct, "This stage"),
          h("span", { class: "muted small", text: `${summary.done} of ${summary.total} done in this stage` }))),
      summary.next.length ? h("ul", { class: "life-lines" }, ...summary.next.map((item) => h("li", { class: "life-line" },
        h("span", { class: "jr-mark", "aria-hidden": "true", text: AREA_MARK[item.area] || "•" }),
        h("div", {}, h("a", { class: "life-line-text", href: `#/journey?tab=stages&open=item:${item.id}`, text: item.title }), h("span", { class: "muted life-line-sub", text: item.stage_title }))))) : null,
      summary.due.length ? h("p", { class: "jr-due-line" }, h("span", { "aria-hidden": "true", text: "💉 " }), h("a", { href: "#/journey?tab=stages", text: plural(summary.due.length, "vaccine or check-up is due soon", "vaccines or check-ups are due soon") })) : null,
      summary.others ? h("p", { class: "muted small", text: plural(summary.others, "more life you look after", "more lives you look after") }) : null);
  }

  Aira.journeyStages = { tab, addLife, milestone, overviewCard };
})();

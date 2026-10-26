/* Aira — Bookings: the pages customers use (aira/suite/bookings/bookings_routes.py).
 *
 * booking_page  pick a service (and a person), a day and a free time, enter your details, book. Shows the price,
 *               the deposit with the business's own payment link, the reference and the link to manage it.
 * booking       one appointment: see it, move it to another free time or cancel it while the business's rules
 *               allow, and pay what is due.
 * Slots always come from the server (actions "slots"), and the server re-checks a time when it is booked.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  if (!P) return;

  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const addDays = (day, n) => { const d = new Date(`${day}T12:00:00`); d.setDate(d.getDate() + n); return isoDay(d); };
  const dayLabel = (day) => new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const minutes = (n) => (n >= 60 ? `${Math.floor(n / 60)} h${n % 60 ? ` ${n % 60} min` : ""}` : `${n} min`);

  // A week of days and their free times, fetched through an action, with a pick callback.
  function timePicker(ctx, { fetch, first, last, onPick }) {
    const { h } = ctx;
    const node = h("div", { class: "pub-form" });
    let start = first;
    let chosenDay = "";
    const render = async () => {
      node.replaceChildren(h("p", { class: "pub-muted", text: "Finding free times…" }));
      let data;
      try { data = await fetch(start); } catch (error) { node.replaceChildren(h("p", { class: "pub-notice bad", text: error.message })); return; }
      const days = data.days || [];
      const withSlots = days.filter((d) => d.slots.length);
      if (!chosenDay || !days.some((d) => d.day === chosenDay)) chosenDay = (withSlots[0] || days[0] || {}).day || "";
      const dayButtons = h("div", { class: "pub-slots", role: "group", "aria-label": "Day" }, ...days.map((d) => {
        const button = h("button", { type: "button", class: "pub-slot", "aria-pressed": String(d.day === chosenDay), disabled: !d.slots.length, text: `${dayLabel(d.day)}${d.slots.length ? "" : " · full"}` });
        button.addEventListener("click", () => { chosenDay = d.day; render(); });
        return button;
      }));
      const current = days.find((d) => d.day === chosenDay);
      const times = current && current.slots.length
        ? h("div", { class: "pub-slots", role: "group", "aria-label": "Time" }, ...current.slots.map((slot) => {
          const button = h("button", { type: "button", class: "pub-slot", "aria-pressed": "false", text: slot.local });
          button.addEventListener("click", () => { node.querySelectorAll('[aria-label="Time"] .pub-slot').forEach((b) => b.setAttribute("aria-pressed", "false")); button.setAttribute("aria-pressed", "true"); onPick(slot, current.day); });
          return button;
        }))
        : h("p", { class: "pub-muted", text: withSlots.length ? "Pick a day." : "No free times this week. Try the next week." });
      const nav = h("div", { class: "pub-btn-row" },
        start > first ? ctx.button("Earlier", () => { start = addDays(start, -7); chosenDay = ""; render(); }, "ghost") : null,
        !last || addDays(start, 7) <= last ? ctx.button("Later", () => { start = addDays(start, 7); chosenDay = ""; render(); }, "ghost") : null);
      node.replaceChildren(dayButtons, times, nav);
    };
    render();
    return node;
  }

  P.register("bookings", "booking_page", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const business = ctx.business;
    root.append(h("h1", { text: data.title || `Book with ${business.name || "us"}` }));
    if (page.welcome) root.append(h("p", { class: "pub-notice", text: page.welcome }));
    if (!page.services.length) { root.append(h("p", { class: "pub-muted", text: "Online booking is not open right now. Please contact us." })); return; }
    const state = { service: null, staffId: "", slot: null };
    const serviceCard = h("section", { class: "pub-card" }, h("h2", { text: "1. Choose a service" }));
    const staffCard = h("section", { class: "pub-card", hidden: true });
    const timeCard = h("section", { class: "pub-card", hidden: true });
    const detailsCard = h("section", { class: "pub-card", hidden: true });
    root.append(serviceCard, staffCard, timeCard, detailsCard);

    const categories = [...new Set(page.services.map((s) => s.category || ""))];
    for (const category of categories) {
      if (category) serviceCard.append(h("h3", { text: category }));
      serviceCard.append(h("div", { class: "pub-grid" }, ...page.services.filter((s) => (s.category || "") === category).map((s) => {
        const item = h("button", { type: "button", class: "pub-item", style: { textAlign: "left", font: "inherit", color: "inherit", cursor: "pointer" } },
          h("strong", { text: s.name }),
          h("span", { class: "pub-muted", text: minutes(s.minutes) }),
          s.description ? h("span", { class: "pub-muted", text: s.description }) : null,
          h("span", { class: "pub-price", text: s.price_cents ? ctx.money(s.price_cents) : "Free" }),
          s.deposit_cents ? h("span", { class: "pub-muted", text: `${ctx.money(s.deposit_cents)} deposit to book` }) : null);
        item.addEventListener("click", () => { serviceCard.querySelectorAll(".pub-item").forEach((n) => n.classList.remove("selected")); item.classList.add("selected"); choose(s); });
        return item;
      })));
    }

    function choose(service) {
      state.service = service;
      state.staffId = "";
      state.slot = null;
      detailsCard.hidden = true;
      if (service.staff.length > 1) {
        const select = h("select", {}, h("option", { value: "", text: "Anyone available" }), ...service.staff.map((p) => h("option", { value: p.id, text: p.name })));
        select.addEventListener("change", () => { state.staffId = select.value; showTimes(); });
        staffCard.replaceChildren(h("h2", { text: "2. With whom?" }), ctx.field("Person", select));
        staffCard.hidden = false;
      } else staffCard.hidden = true;
      showTimes();
      timeCard.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function showTimes() {
      const last = addDays(page.today, page.max_days_ahead);
      timeCard.replaceChildren(h("h2", { text: `${state.service.staff.length > 1 ? "3" : "2"}. Pick a time` }), h("p", { class: "pub-muted", text: `Times are in ${page.timezone}.` }),
        timePicker(ctx, {
          first: page.today,
          last,
          fetch: (day) => ctx.action("slots", { service_id: state.service.id, staff_id: state.staffId, day, days: 7 }),
          onPick: (slot, day) => { state.slot = { ...slot, day }; showDetails(); },
        }));
      timeCard.hidden = false;
    }

    function showDetails() {
      const name = h("input", { autocomplete: "name", required: true });
      const email = h("input", { type: "email", autocomplete: "email" });
      const phone = h("input", { type: "tel", autocomplete: "tel" });
      const note = h("textarea", { maxlength: 1000 });
      const s = state.service;
      detailsCard.replaceChildren(
        h("h2", { text: "Your details" }),
        h("p", {}, h("strong", { text: `${s.name}, ${dayLabel(state.slot.day)} at ${state.slot.local}` }), h("span", { class: "pub-muted", text: ` · ${minutes(s.minutes)}${s.price_cents ? ` · ${ctx.money(s.price_cents)}` : ""}` })),
        h("div", { class: "pub-form" }, ctx.field("Name", name), ctx.field("Email", email, "For your confirmation and reminder."), ctx.field("Phone", phone), ctx.field("Anything we should know?", note)),
        h("div", { class: "pub-btn-row" }, ctx.button(s.deposit_cents ? `Book (deposit ${ctx.money(s.deposit_cents)})` : "Book", async () => {
          if (!name.value.trim()) { name.focus(); throw new Error("Enter your name."); }
          if (!email.value.trim() && !phone.value.trim()) { email.focus(); throw new Error("Enter an email address or a phone number."); }
          try {
            const made = await ctx.action("book", { service_id: s.id, staff_id: state.staffId, start: state.slot.start, name: name.value.trim(), email: email.value.trim(), phone: phone.value.trim(), note: note.value.trim() });
            done(made);
          } catch (error) {
            if (/no longer available/i.test(error.message)) showTimes();
            throw error;
          }
        })),
        h("p", { class: "pub-muted pub-hint", text: `You can cancel or change online until ${page.cancel_notice_hours} hours before.` }));
      detailsCard.hidden = false;
      detailsCard.scrollIntoView({ behavior: "smooth", block: "start" });
      name.focus();
    }

    function done(made) {
      const b = made.booking;
      const manage = `${location.origin}${made.manage_path}`;
      const due = b.deposit_cents || b.price_cents;
      root.replaceChildren(
        h("p", { class: "pub-notice good", role: "status", text: "You're booked." }),
        h("section", { class: "pub-card" },
          h("h1", { text: b.service_name }),
          h("p", {}, h("strong", { text: b.local_start }), h("span", { class: "pub-muted", text: ` with ${b.staff_name}` })),
          h("p", {}, "Reference ", h("span", { class: "pub-badge", text: b.reference })),
          b.price_cents ? h("p", { text: `Price ${ctx.money(b.price_cents)}${b.deposit_cents ? ` · deposit ${ctx.money(b.deposit_cents)} to secure your time` : ""}` }) : null,
          h("div", { class: "pub-btn-row" }, h("a", { class: "pub-btn secondary", href: manage, text: "Manage your booking" }), ctx.button("Copy link", () => ctx.copy(manage), "ghost")),
          h("p", { class: "pub-muted pub-hint", text: "Keep this link: it is how you cancel or change your booking." })),
        due ? ctx.paymentBox(due, b.reference) : null,
      );
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  });

  P.register("bookings", "booking", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const render = (b) => {
      const tone = b.status === "cancelled" || b.status === "no_show" ? "bad" : b.status === "completed" || b.status === "confirmed" ? "good" : "";
      const due = b.paid_by_package ? 0 : Math.max(0, (b.paid_cents ? b.price_cents : b.deposit_cents || b.price_cents) - b.paid_cents);
      const active = b.status === "booked" || b.status === "confirmed";
      const actions = h("div", { class: "pub-form" });
      root.replaceChildren(
        h("section", { class: "pub-card" },
          h("div", {}, h("span", { class: `pub-badge ${tone}`, text: b.status.replace("_", " ") })),
          h("h1", { text: b.service_name }),
          h("p", {}, h("strong", { text: b.local_start }), h("span", { class: "pub-muted", text: ` (${page.timezone}) with ${b.staff_name}` })),
          h("p", {}, "Reference ", h("span", { class: "pub-badge", text: b.reference })),
          b.price_cents ? h("p", { text: b.paid_by_package ? "Paid with your package." : b.paid_cents ? `Paid ${ctx.money(b.paid_cents)} of ${ctx.money(b.price_cents)}` : `Price ${ctx.money(b.price_cents)}${b.deposit_cents ? ` · deposit ${ctx.money(b.deposit_cents)}` : ""}` }) : null),
        active && due ? ctx.paymentBox(due, b.reference) : null,
        active ? h("section", { class: "pub-card" }, h("h2", { text: "Need to change it?" }), b.can_change ? actions : h("p", { class: "pub-muted", text: `Online changes closed ${page.cancel_notice_hours} hours before the appointment. Please contact us.` })) : null,
      );
      if (!active || !b.can_change) return;
      actions.append(h("p", { class: "pub-muted", text: `You can move or cancel until ${ctx.datetime(b.change_until)}.` }), h("div", { class: "pub-btn-row" },
        ctx.button("Move to another time", async () => {
          let picked = null;
          const confirm = ctx.button("Move my booking", async () => {
            if (!picked) throw new Error("Pick a new time.");
            const moved = await ctx.action("reschedule", { start: picked.start });
            render(moved.booking);
            ctx.notice("Your booking was moved.", "good");
          });
          actions.replaceChildren(timePicker(ctx, { first: page.today, last: "", fetch: (day) => ctx.action("slots", { day, days: 7 }), onPick: (slot) => { picked = slot; } }), h("div", { class: "pub-btn-row" }, confirm, ctx.button("Back", async () => render(b), "ghost")));
        }, "secondary"),
        ctx.button("Cancel booking", async () => {
          const reason = h("textarea", { maxlength: 500 });
          actions.replaceChildren(ctx.field("Reason (optional)", reason), h("div", { class: "pub-btn-row" },
            ctx.button("Yes, cancel it", async () => { const out = await ctx.action("cancel", { reason: reason.value.trim() }); render(out.booking); ctx.notice("Your booking is cancelled.", "good"); }),
            ctx.button("Keep my booking", async () => render(b), "ghost")));
        }, "ghost")));
    };
    render(page.booking);
  });
})();

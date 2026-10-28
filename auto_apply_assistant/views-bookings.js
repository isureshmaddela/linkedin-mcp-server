/* Aira — Bookings (#/bookings): appointments that pay, for salons, clinics, studios, tutors and consultants.
 *
 * Tabs (?tab=): overview (today, this week, revenue, no-shows, the booking page to share, setup), calendar (a week
 * per person), appointments (search, filters, CSV), waiting list (customers waiting for a time on a full day),
 * services, staff (hours, commission and time off), customers (visits, spend, notes, packages), packages, team pay
 * (takings and commission per person, CSV) and settings (booking rules). A booking opens in a drawer: confirm,
 * complete, no-show, cancel, reschedule, take the deposit or the full price, redeem a package, notes, the
 * customer's own link.
 * Everything runs through /api/bookings/* (aira/suite/bookings/bookings_routes.py). DOM is built with h() and text.
 *
 * In the Aira frame, "+ New" and the search box for all of Commerce are set up in views-shop.js; links from them
 * open here once: new=booking or new=service opens the form, booking=, customer= or service=<id> the record.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const call = (path, opts) => S.api("bookings", path, opts);
  const TABS = [
    { id: "overview", label: "Overview", icon: "dashboard" },
    { id: "calendar", label: "Calendar", icon: "interviews" },
    { id: "appointments", label: "Appointments", icon: "bookings" },
    { id: "waitlist", label: "Waiting list", icon: "planner" },
    { id: "services", label: "Services", icon: "careers" },
    { id: "staff", label: "Staff", icon: "family" },
    { id: "customers", label: "Customers", icon: "recruiters" },
    { id: "packages", label: "Packages", icon: "events" },
    { id: "pay", label: "Team pay", icon: "stocks" },
    { id: "settings", label: "Settings", icon: "settings" },
  ];
  // A link from "+ New" or the search box opens a form or a record once (see the view at the end).
  const ONCE = ["new", "booking", "customer", "service"];
  let pending = {};
  const take = (key, value) => { const found = pending[key]; if (!found || (value && found !== value)) return ""; delete pending[key]; return found; };
  const later = (fn) => { Promise.resolve().then(fn).catch((error) => console.warn(error)); };
  // On a phone a wide table keeps its key columns: cm-hide-<n> hides the nth column under 560px (app-commerce.css).
  const narrow = (table, ...columns) => h("div", { class: ["cm-narrow", ...columns.map((n) => `cm-hide-${n}`)].join(" ") }, table);
  const STATUS_TONES = { booked: "warn", confirmed: "info", completed: "good", cancelled: "bad", no_show: "bad" };
  const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const status = (value) => S.pill(value, STATUS_TONES);
  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const addDays = (day, n) => { const d = new Date(`${day}T12:00:00`); d.setDate(d.getDate() + n); return isoDay(d); };
  // Today in the business's time zone (the server says so with its lists), not this computer's date.
  let businessDay = "";
  const learnDay = (data) => { if (data && /^\d{4}-\d{2}-\d{2}$/.test(data.today || "")) businessDay = data.today; return data; };
  const today = () => businessDay || isoDay(new Date());
  const paidLabel = (b) => (b.payment_kind === "package" ? "Package" : b.paid_cents ? S.money(b.paid_cents) : b.price_cents ? "Unpaid" : "Free");

  let rerender = () => {};

  // ------------------------------------------------------------------ overview
  async function overview(body) {
    const data = learnDay(await call("/overview"));
    const revenue = data.revenue;
    const steps = data.checklist || [];
    body.append(
      S.kpis([
        { label: "Today", value: data.today_bookings.length, hint: data.timezone },
        { label: "Next 7 days", value: data.bookings_this_week },
        { label: "Revenue this month", value: S.money(revenue.this_month_cents, revenue.currency), tone: "good" },
        { label: "No-show rate (90 days)", value: data.no_show_rate === null ? "-" : `${data.no_show_rate}%`, tone: data.no_show_rate >= 10 ? "warn" : "" },
        data.to_confirm ? { label: "Waiting for you to confirm", value: data.to_confirm, tone: "warn" } : null,
        data.unpaid_completed ? { label: "Completed, money still due", value: data.unpaid_completed, tone: "bad" } : null,
        data.waiting ? { label: "On the waiting list", value: data.waiting, tone: "warn", hint: "Waiting list tab" } : null,
      ]),
      S.share({
        title: "Your booking page",
        text: "Customers pick a service, a time and book themselves. Put the link on your website, Instagram bio, Google profile and email signature.",
        link: data.page_link,
        onCreate: async () => (await call("/page-link", { method: "POST", body: {} })).link,
        onRevoke: async () => call("/page-link", { method: "DELETE" }),
      }),
    );
    if (steps.some((step) => !step.done)) body.append(S.section("Get set up", S.checklist(steps)));
    body.append(S.grid(
      S.section("Today", narrow(S.table({ columns: [{ key: "local_time", label: "Time" }, { key: "customer_name", label: "Customer" }, { key: "service_name", label: "Service" }, { key: "staff_name", label: "With" }, { key: "status", label: "Status", render: (b) => status(b.status) }], rows: data.today_bookings, empty: Aira.ui.empty("Nothing booked today", "Share your booking page to fill your calendar, or add a booking taken by phone.", S.button("New booking", () => newBooking({}), { kind: "secondary" })), onRow: (b) => openBooking(b.id) }), 4)),
      S.section("Busiest services (30 days)", S.table({ columns: [{ key: "service_name", label: "Service" }, { key: "bookings", label: "Bookings", num: true }], rows: data.busiest, empty: "No bookings yet" })),
    ));
    body.append(S.section("Coming up", narrow(S.table({ columns: [{ key: "local_start", label: "When" }, { key: "customer_name", label: "Customer" }, { key: "service_name", label: "Service" }, { key: "status", label: "Status", render: (b) => status(b.status) }, { key: "paid", label: "Paid", render: paidLabel }], rows: data.upcoming, empty: "No upcoming bookings", onRow: (b) => openBooking(b.id) }), 3, 5)));
  }

  // ------------------------------------------------------------------ calendar
  async function calendar(body, route) {
    const staffId = route.query.staff || "";
    // Without a start the server begins on today in the business's time zone (not this computer's).
    const data = await call(`/agenda?days=7${route.query.start ? `&start=${encodeURIComponent(route.query.start)}` : ""}${staffId ? `&staff_id=${encodeURIComponent(staffId)}` : ""}`);
    const start = data.days.length ? data.days[0].day : today();
    const go = (query) => Aira.router.go(["bookings"], { tab: "calendar", start, staff: staffId, ...query });
    const people = Aira.ui.select([["", "Everyone"], ...data.staff.map((p) => [p.id, p.name])], staffId, { "aria-label": "Person" });
    people.addEventListener("change", () => go({ staff: people.value }));
    const colors = Object.fromEntries(data.staff.map((p) => [p.id, p.color]));
    body.append(
      h("div", { class: "btn-row cm-filters" },
        S.button("Previous week", () => go({ start: addDays(start, -7) }), { kind: "secondary sm" }),
        S.button("This week", () => go({ start: "" }), { kind: "secondary sm" }),
        S.button("Next week", () => go({ start: addDays(start, 7) }), { kind: "secondary sm" }),
        people,
        S.button("New booking", () => newBooking({ day: start }), { kind: "sm cm-tab-new" }),
        h("span", { class: "muted small", text: `Times in ${data.timezone}` })),
      h("div", { class: "cm-week" },
        ...data.days.map((day) => h("div", { class: `card cm-day${day.bookings.length ? "" : " free"}` },
          h("strong", { text: day.label }),
          ...(day.bookings.length ? day.bookings.map((b) => h("button", { type: "button", class: `kcard cm-slot${b.status === "cancelled" ? " cancelled" : ""}`, style: { borderLeftColor: colors[b.staff_id] || "var(--brand)" }, onclick: () => openBooking(b.id) },
            h("strong", { text: `${b.local_time}-${b.local_end} ${b.customer_name}` }), h("span", { text: `${b.service_name} · ${b.staff_name}` }), status(b.status))) : [h("span", { class: "muted small", text: "Free" })]),
          S.button("Book", () => newBooking({ day: day.day }), { kind: "ghost sm", title: `Book on ${day.label}` })))),
    );
  }

  // ------------------------------------------------------------------ appointments
  async function appointments(body, route) {
    const q = route.query;
    const when = q.when || "upcoming";
    const params = new URLSearchParams({ when, ...(q.status ? { status: q.status } : {}), ...(q.q ? { q: q.q } : {}) });
    const data = await call(`/bookings?${params}`);
    const search = h("input", { type: "search", placeholder: "Name, email, phone or reference", value: q.q || "", "data-search": "1", "aria-label": "Search bookings" });
    const whenSelect = Aira.ui.select([["upcoming", "Upcoming"], ["past", "Past"], ["all", "All"]], when, { "aria-label": "When" });
    const statusSelect = Aira.ui.select([["", "Any status"], ...S.statusOptions(["booked", "confirmed", "completed", "cancelled", "no_show"])], q.status || "", { "aria-label": "Status" });
    const apply = () => Aira.router.go(["bookings"], { tab: "appointments", when: whenSelect.value, status: statusSelect.value, q: search.value.trim() });
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); });
    whenSelect.addEventListener("change", apply);
    statusSelect.addEventListener("change", apply);
    body.append(
      h("div", { class: "btn-row cm-filters" }, search, whenSelect, statusSelect, S.button("New booking", () => newBooking({}), { kind: "cm-tab-new" }), S.button("Download CSV", () => Aira.download("/api/bookings/bookings?format=csv", "bookings.csv"), { kind: "secondary" })),
      narrow(S.table({
        columns: [
          { key: "local_start", label: "When" },
          { key: "customer_name", label: "Customer" },
          { key: "service_name", label: "Service" },
          { key: "staff_name", label: "With" },
          { key: "status", label: "Status", render: (b) => status(b.status) },
          { key: "paid", label: "Paid", render: paidLabel },
          { key: "reference", label: "Ref" },
        ],
        rows: data.bookings,
        empty: q.q || q.status || when !== "upcoming"
          ? Aira.ui.empty("No bookings match", "Change the filters, or add a booking.", h("a", { class: "btn secondary", href: "#/bookings?tab=appointments", text: "Clear the filters" }))
          : Aira.ui.empty("No upcoming bookings", "Share your booking page, or add a booking taken by phone or in person.", S.button("New booking", () => newBooking({}), { kind: "secondary" })),
        onRow: (b) => openBooking(b.id),
      }), 4, 6, 7),
    );
    if (take("new", "booking")) later(() => newBooking({}));
    const wanted = take("booking");
    if (wanted) later(() => openBooking(wanted));
  }

  // ------------------------------------------------------------------ a booking
  async function openBooking(id) {
    const { booking } = await call(`/bookings/${id}`);
    const active = booking.status === "booked" || booking.status === "confirmed";
    const act = async (path, body, message) => { await call(`/bookings/${id}${path}`, { method: "POST", body: body || {} }); if (message) Aira.toast(message, "success"); Aira.closeDrawer(); rerender(); openBooking(id); };
    const setStatus = async (value, message) => { await call(`/bookings/${id}`, { method: "PATCH", body: { status: value } }); Aira.toast(message, "success"); Aira.closeDrawer(); rerender(); openBooking(id); };
    const statusRow = h("div", { class: "btn-row" },
      booking.status === "booked" ? S.button("Confirm", () => setStatus("confirmed", "Confirmed.")) : null,
      active || booking.status === "no_show" ? S.button("Completed", () => setStatus("completed", "Marked completed."), { kind: "secondary" }) : null,
      active ? S.button("No-show", () => setStatus("no_show", "Marked as a no-show."), { kind: "secondary" }) : null,
      active ? S.button("Reschedule", () => rescheduleBooking(booking), { kind: "secondary" }) : null,
      active ? S.button("Cancel booking", async () => {
        const reason = await S.edit({ title: "Cancel this booking", fields: [{ name: "reason", label: "Reason (shown to the customer)", type: "textarea", rows: 2 }], submitLabel: "Cancel booking", onSave: (v) => v });
        if (reason) await act("/cancel", reason, "Cancelled. The customer's message is in the outbox.");
      }, { kind: "danger secondary" }) : null);
    const payRow = h("div", { class: "btn-row" });
    if (booking.payment_kind === "package") payRow.append(h("span", { class: "muted", text: "Paid with a package session." }), S.button("Undo", () => act("/unpay", {}, "Package session returned."), { kind: "ghost sm" }));
    else if (booking.paid_cents) payRow.append(...[h("span", { text: `Paid ${S.money(booking.paid_cents)} (${booking.payment_kind})` }), booking.due_cents ? S.button(`Take the rest (${S.money(booking.due_cents)})`, () => act("/pay", { kind: "full" }, "Paid in full.")) : null, S.button("Mark unpaid", () => act("/unpay", {}, "Payment removed."), { kind: "ghost sm" })].filter(Boolean));
    else {
      if (booking.deposit_cents) payRow.append(S.button(`Deposit paid (${S.money(booking.deposit_cents)})`, () => act("/pay", { kind: "deposit" }, "Deposit recorded."), { kind: "secondary" }));
      if (booking.price_cents) payRow.append(S.button(`Paid in full (${S.money(booking.price_cents)})`, () => act("/pay", { kind: "full" }, "Payment recorded.")));
      payRow.append(S.button("Other amount", async () => {
        const values = await S.edit({ title: "Record a payment", fields: [{ name: "amount_cents", label: "Amount", type: "money", required: true }], onSave: (v) => v });
        if (values) await act("/pay", { kind: "amount", amount_cents: values.amount_cents }, "Payment recorded.");
      }, { kind: "ghost" }));
      if (booking.packages && booking.packages.length) {
        const pick = Aira.ui.select(booking.packages.map((p) => [p.id, `${p.name} (${p.remaining} left)`]), booking.packages[0].id, { "aria-label": "Package" });
        payRow.append(pick, S.button("Use a package session", () => act("/redeem", { customer_package_id: pick.value }, "Package session used."), { kind: "secondary" }));
      }
    }
    const notes = S.form([{ name: "note", label: "Customer's note", type: "textarea", rows: 2 }, { name: "internal_note", label: "Private note (only your team sees it)", type: "textarea", rows: 3 }], booking);
    const linkCard = S.share({ title: "The customer's page", text: "Where the customer sees, cancels or moves this booking.", link: booking.link, onCreate: async () => (await call(`/bookings/${id}/link`, { method: "POST", body: {} })).link });
    Aira.drawer({
      title: `${booking.customer_name} · ${booking.service_name}`,
      subtitle: h("span", {}, status(booking.status), ` ${booking.local_start} · ${booking.reference}`),
      body: h("div", { class: "suite-body" },
        Aira.ui.kv([["With", booking.staff_name], ["Email", booking.customer_email], ["Phone", booking.customer_phone], ["Price", S.money(booking.price_cents)], ["Deposit", booking.deposit_cents ? S.money(booking.deposit_cents) : ""], ["Booked", `${S.when(booking.created_at)} (${booking.source === "public" ? "online" : "by you"})`], ["Cancelled", booking.cancelled_at ? `${S.when(booking.cancelled_at)}${booking.cancel_reason ? `: ${booking.cancel_reason}` : ""}` : ""]]),
        S.section("Status", statusRow),
        S.section("Payment", payRow),
        S.section("Notes", notes.node, h("div", { class: "btn-row" }, S.button("Save notes", async () => { await call(`/bookings/${id}`, { method: "PATCH", body: notes.read() }); Aira.toast("Saved.", "success"); }, { kind: "secondary" }))),
        linkCard),
    });
  }

  // A day picker and the free times for a service (shared by new bookings and rescheduling).
  function slotPicker({ serviceId, staffId = "", day = today(), exclude = "", onPick }) {
    const dayInput = h("input", { type: "date", value: day, "aria-label": "Day" });
    const holder = h("div", { class: "suite-body" });
    let chosen = null;
    const load = async () => {
      if (!serviceId()) { holder.replaceChildren(h("p", { class: "muted", text: "Choose a service first." })); return; }
      holder.replaceChildren(Aira.ui.skeleton(1));
      const params = new URLSearchParams({ service_id: serviceId(), day: dayInput.value || today(), days: "1", ...(staffId() ? { staff_id: staffId() } : {}), ...(exclude ? { exclude } : {}) });
      const data = await call(`/slots?${params}`);
      const slots = data.days[0].slots;
      if (!slots.length) { holder.replaceChildren(h("p", { class: "muted", text: "No free times that day. Try another day, or book outside hours." })); return; }
      holder.replaceChildren(h("div", { class: "btn-row" }, ...slots.map((slot) => {
        const button = h("button", { type: "button", class: "btn secondary sm", text: slot.local, "aria-pressed": "false" });
        button.addEventListener("click", () => { holder.querySelectorAll("button").forEach((b) => { b.setAttribute("aria-pressed", "false"); b.classList.add("secondary"); }); button.setAttribute("aria-pressed", "true"); button.classList.remove("secondary"); chosen = slot; onPick(slot); });
        return button;
      })), h("span", { class: "muted small", text: `Times in ${data.timezone}` }));
    };
    dayInput.addEventListener("change", load);
    return { node: h("div", { class: "suite-body" }, Aira.ui.field("Day", dayInput), holder), load, chosen: () => chosen, day: () => dayInput.value };
  }

  async function newBooking({ day = "", customer = null, serviceId = "", staffId = "" } = {}) {
    const [{ services }, { staff }] = await Promise.all([call("/services").then(learnDay), call("/staff")]);
    day = day || today();
    const usable = services.filter((s) => s.active);
    if (!usable.length) { Aira.toast("Add a service first (Services tab)."); Aira.router.go(["bookings"], { tab: "services" }); return; }
    const firstService = usable.some((s) => s.id === serviceId) ? serviceId : usable[0].id;
    const serviceSelect = Aira.ui.select(usable.map((s) => [s.id, `${s.name} · ${s.minutes} min · ${S.money(s.price_cents)}`]), firstService, { "aria-label": "Service" });
    const staffSelect = Aira.ui.select([["", "Anyone available"], ...staff.filter((p) => p.active).map((p) => [p.id, p.name])], staff.some((p) => p.id === staffId && p.active) ? staffId : "", { "aria-label": "With" });
    let picked = null;
    const picker = slotPicker({ serviceId: () => serviceSelect.value, staffId: () => staffSelect.value, day, onPick: (slot) => { picked = slot; } });
    serviceSelect.addEventListener("change", picker.load);
    staffSelect.addEventListener("change", picker.load);
    const outside = h("input", { type: "checkbox" });
    const time = h("input", { type: "time", value: "18:00" });
    const who = S.form([{ name: "name", label: "Customer name", required: true }, { name: "email", label: "Email", type: "email" }, { name: "phone", label: "Phone", type: "tel" }, { name: "note", label: "Note", type: "textarea", rows: 2 }], customer ? { name: customer.name, email: customer.email, phone: customer.phone } : {});
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    Aira.modal({
      title: "New booking",
      wide: true,
      body: h("div", { class: "suite-form" }, Aira.ui.field("Service", serviceSelect), Aira.ui.field("With", staffSelect), picker.node,
        h("label", { class: "suite-check" }, outside, h("span", { text: "Outside normal hours (a walk-in or a special time)" })), Aira.ui.field("Time (outside hours)", time, "Used only when the box above is ticked."),
        who.node, error),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Book", kind: "primary", onClick: async () => {
          error.hidden = true;
          const values = who.read();
          const body = { service_id: serviceSelect.value, staff_id: staffSelect.value, ...values };
          if (outside.checked) Object.assign(body, { outside_hours: true, day: picker.day(), time: time.value });
          else if (picked) body.start = picked.start;
          else { error.textContent = "Pick a time, or tick outside hours."; error.hidden = false; return false; }
          if (!values.name) { error.textContent = "Enter the customer's name."; error.hidden = false; return false; }
          try { const made = await call("/bookings", { method: "POST", body }); Aira.toast(`Booked ${made.booking.reference}.`, "success"); rerender(); return true; } catch (err) { error.textContent = err.message; error.hidden = false; return false; }
        } },
      ],
    });
    picker.load();
  }

  async function rescheduleBooking(booking) {
    let picked = null;
    const picker = slotPicker({ serviceId: () => booking.service_id, staffId: () => "", day: booking.start_at.slice(0, 10), exclude: booking.id, onPick: (slot) => { picked = slot; } });
    Aira.modal({
      title: `Move ${booking.reference}`,
      body: h("div", { class: "suite-form" }, h("p", { class: "muted", text: `Now: ${booking.local_start} with ${booking.staff_name}. The customer is told in a message waiting in the outbox.` }), picker.node),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Move booking", kind: "primary", onClick: async () => {
          if (!picked) { Aira.toast("Pick a new time."); return false; }
          await call(`/bookings/${booking.id}/reschedule`, { method: "POST", body: { start: picked.start } });
          Aira.toast("Moved.", "success");
          Aira.closeDrawer();
          rerender();
          return true;
        } },
      ],
    });
    picker.load();
  }

  // ------------------------------------------------------------------ services
  async function services(body) {
    const [{ services: rows }, { staff }] = await Promise.all([call("/services"), call("/staff")]);
    const names = Object.fromEntries(staff.map((p) => [p.id, p.name]));
    const fields = () => [
      { name: "name", label: "Name", required: true, placeholder: "Haircut and style" },
      { name: "category", label: "Category", placeholder: "Hair" },
      { name: "description", label: "Description (on your booking page)", type: "textarea", rows: 3 },
      { name: "minutes", label: "Length (minutes)", type: "number", min: 5, max: 720, required: true },
      { name: "buffer_minutes", label: "Buffer after (minutes)", type: "number", min: 0, max: 240, hint: "Clean-up or travel time nobody can book." },
      { name: "price_cents", label: "Price", type: "money" },
      { name: "deposit_cents", label: "Deposit to secure the time", type: "money", hint: "Shown on the booking page with your payment link. Reduces no-shows." },
      { name: "active", label: "Bookable", type: "checkbox" },
      ...(staff.length ? [{ type: "heading", label: "Who offers it (none ticked = everyone)" }, ...staff.map((p) => ({ name: `staff_${p.id}`, label: p.name, type: "checkbox" }))] : []),
    ];
    const toBody = (values) => {
      const body = { staff_ids: staff.filter((p) => values[`staff_${p.id}`]).map((p) => p.id) };
      for (const [key, value] of Object.entries(values)) if (!key.startsWith("staff_")) body[key] = value;
      return body;
    };
    const edit = (row) => S.edit({
      title: row ? `Edit ${row.name}` : "New service",
      fields: fields(),
      values: row ? { ...row, ...Object.fromEntries((row.staff_ids || []).map((id) => [`staff_${id}`, true])) } : { minutes: 60, buffer_minutes: 0, active: true },
      onSave: async (values) => { await call(row ? `/services/${row.id}` : "/services", { method: row ? "PATCH" : "POST", body: toBody(values) }); rerender(); },
    });
    body.append(
      h("div", { class: "btn-row" }, S.button("New service", () => edit(null))),
      narrow(S.table({
        columns: [
          { key: "name", label: "Service" },
          { key: "category", label: "Category" },
          { key: "minutes", label: "Length", render: (s) => `${s.minutes} min${s.buffer_minutes ? ` + ${s.buffer_minutes}` : ""}` },
          { key: "price_cents", label: "Price", num: true, render: (s) => S.money(s.price_cents) },
          { key: "deposit_cents", label: "Deposit", num: true, render: (s) => (s.deposit_cents ? S.money(s.deposit_cents) : "") },
          { key: "staff", label: "Who", render: (s) => (s.staff_ids.length ? s.staff_ids.map((id) => names[id] || "?").join(", ") : "Everyone") },
          { key: "active", label: "", render: (s) => (s.active ? S.pill("active") : S.pill("archived")) },
          { key: "x", label: "", render: (s) => S.button("Delete", async () => { if (await S.confirmDelete(s.name)) { const out = await call(`/services/${s.id}`, { method: "DELETE" }); Aira.toast(out.deactivated ? "It has bookings, so it was made unbookable instead." : "Deleted.", "success"); rerender(); } }, { kind: "ghost sm" }) },
        ],
        rows,
        empty: Aira.ui.empty("No services yet", "Add what customers can book: its length, price and an optional deposit.", S.button("Add a service", () => edit(null))),
        onRow: edit,
      }), 2, 5, 6),
    );
    if (take("new", "service")) later(() => edit(null));
    const serviceId = take("service");
    const wanted = serviceId && rows.find((row) => row.id === serviceId);
    if (wanted) later(() => edit(wanted));
  }

  // ------------------------------------------------------------------ staff
  const rangesText = (hours, weekday) => hours.filter((r) => r.weekday === weekday).map((r) => `${r.start}-${r.end}`).join(", ");
  function parseRanges(text, weekday) {
    return String(text || "").split(",").map((part) => part.trim()).filter(Boolean).map((part) => {
      const [start, end] = part.split("-").map((v) => v.trim());
      if (!start || !end) throw new Error(`${WEEKDAYS[weekday]}: write ranges like 09:00-12:00, 13:00-17:00.`);
      return { weekday, start, end };
    });
  }
  async function staffTab(body) {
    const { staff } = learnDay(await call("/staff"));
    const { time_off: timeOff } = await call(`/time-off?from=${today()}`);
    const names = Object.fromEntries(staff.map((p) => [p.id, p.name]));
    const edit = (row) => S.edit({
      title: row ? `Edit ${row.name}` : "Add a person",
      fields: [
        { name: "name", label: "Name", required: true },
        { name: "email", label: "Email (never shown to customers)", type: "email" },
        { name: "color", label: "Calendar colour", type: "color" },
        { name: "active", label: "Takes bookings", type: "checkbox" },
        { name: "commission_percent", label: "Commission (percent of what they bring in)", type: "number", min: 0, max: 100, step: "0.5", hint: "For Team pay. 0 if you pay them another way. A new rate counts from now on: earlier appointments keep the rate they had." },
        { type: "heading", label: "Weekly hours (for example 09:00-12:00, 13:00-17:00; empty = off)" },
        ...WEEKDAYS.map((name, index) => ({ name: `day_${index}`, label: name, placeholder: index < 5 ? "09:00-17:00" : "" })),
      ],
      values: row ? { ...row, ...Object.fromEntries(WEEKDAYS.map((_, i) => [`day_${i}`, rangesText(row.hours, i)])) } : { color: "#2563eb", active: true, commission_percent: 0, ...Object.fromEntries(WEEKDAYS.map((_, i) => [`day_${i}`, i < 5 ? "09:00-17:00" : ""])) },
      onSave: async (values) => {
        const hours = WEEKDAYS.flatMap((_, i) => parseRanges(values[`day_${i}`], i));
        const payload = { name: values.name, email: values.email, color: values.color, active: values.active, commission_percent: values.commission_percent === null ? 0 : values.commission_percent, hours };
        await call(row ? `/staff/${row.id}` : "/staff", { method: row ? "PATCH" : "POST", body: payload });
        rerender();
      },
    });
    const addTimeOff = () => S.edit({
      title: "Time off",
      fields: [{ name: "staff_id", label: "Who", type: "select", options: [["", "The whole business (closed)"], ...staff.map((p) => [p.id, p.name])] }, { name: "start_day", label: "First day", type: "date", required: true }, { name: "end_day", label: "Last day", type: "date" }, { name: "reason", label: "Reason (private)" }],
      values: { start_day: today() },
      onSave: async (values) => { await call("/time-off", { method: "POST", body: values }); rerender(); },
    });
    body.append(
      h("div", { class: "btn-row" }, S.button("Add a person", () => edit(null)), S.button("Add time off", addTimeOff, { kind: "secondary" })),
      S.table({
        columns: [
          { key: "name", label: "Name", render: (p) => h("span", {}, h("span", { style: { display: "inline-block", width: "10px", height: "10px", borderRadius: "50%", background: p.color, marginRight: "6px" } }), p.name) },
          { key: "hours", label: "Hours", render: (p) => (p.hours.length ? WEEKDAYS.map((d, i) => (rangesText(p.hours, i) ? `${d.slice(0, 3)} ${rangesText(p.hours, i)}` : "")).filter(Boolean).join(" · ") : h("span", { class: "muted", text: "No hours yet: nobody can book them" })) },
          { key: "commission_percent", label: "Commission", num: true, render: (p) => (p.commission_percent ? `${p.commission_percent}%` : "") },
          { key: "active", label: "", render: (p) => (p.active ? S.pill("active") : S.pill("archived")) },
          { key: "x", label: "", render: (p) => S.button("Remove", async () => { if (await S.confirmDelete(p.name)) { const out = await call(`/staff/${p.id}`, { method: "DELETE" }); Aira.toast(out.deactivated ? "They have past bookings, so they were made inactive." : "Removed.", "success"); rerender(); } }, { kind: "ghost sm" }) },
        ],
        rows: staff,
        empty: Aira.ui.empty("Nobody yet", "Add yourself (and anyone else who takes appointments) with weekly hours.", S.button("Add a person", () => edit(null))),
        onRow: edit,
      }),
      S.section("Upcoming time off", S.table({ columns: [{ key: "staff_id", label: "Who", render: (t) => (t.staff_id ? names[t.staff_id] || "?" : "Whole business") }, { key: "start_day", label: "From" }, { key: "end_day", label: "To" }, { key: "reason", label: "Reason" }, { key: "x", label: "", render: (t) => S.button("Remove", async () => { await call(`/time-off/${t.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" }) }], rows: timeOff, empty: "No time off planned" })),
    );
  }

  // ------------------------------------------------------------------ customers
  async function customers(body, route) {
    const q = route.query.q || "";
    const data = await call(`/customers${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    const search = h("input", { type: "search", placeholder: "Search customers", value: q, "data-search": "1", "aria-label": "Search customers" });
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") Aira.router.go(["bookings"], { tab: "customers", q: search.value.trim() }); });
    body.append(
      h("div", { class: "btn-row cm-filters" }, search, S.button("Download CSV", () => Aira.download("/api/bookings/customers?format=csv", "customers.csv"), { kind: "secondary" })),
      narrow(S.table({
        columns: [
          { key: "name", label: "Customer" },
          { key: "email", label: "Email" },
          { key: "phone", label: "Phone" },
          { key: "visits", label: "Visits", num: true },
          { key: "no_shows", label: "No-shows", num: true },
          { key: "spend_cents", label: "Spent", num: true, render: (c) => S.money(c.spend_cents) },
          { key: "last_visit", label: "Last visit", render: (c) => S.day(c.last_visit) },
          { key: "next_booking", label: "Next", render: (c) => S.day(c.next_booking) },
        ],
        rows: data.customers,
        empty: q
          ? Aira.ui.empty("No customers match", "Try a name, an email or a phone number.", h("a", { class: "btn secondary", href: "#/bookings?tab=customers", text: "Show everyone" }))
          : Aira.ui.empty("No customers yet", "Customers appear here when they book, or when you add a booking for them.", S.button("New booking", () => newBooking({}), { kind: "secondary" })),
        onRow: (c) => openCustomer(c.id),
      }), 2, 3, 5, 7),
    );
    const wanted = take("customer");
    if (wanted) later(() => openCustomer(wanted));
  }

  async function openCustomer(id) {
    const [{ customer }, { packages }] = await Promise.all([call(`/customers/${id}`), call("/packages")]);
    const form = S.form([{ name: "name", label: "Name", required: true }, { name: "email", label: "Email", type: "email" }, { name: "phone", label: "Phone", type: "tel" }, { name: "tags", label: "Tags", placeholder: "vip, colour" }, { name: "notes", label: "Notes (private)", type: "textarea", rows: 4 }], { ...customer, tags: customer.tags.join(", ") });
    const sellable = packages.filter((p) => p.active);
    const pick = sellable.length ? Aira.ui.select(sellable.map((p) => [p.id, `${p.name} · ${p.sessions} sessions · ${S.money(p.price_cents)}`]), sellable[0].id, { "aria-label": "Package" }) : null;
    const paid = h("input", { type: "checkbox", checked: true });
    Aira.drawer({
      title: customer.name,
      subtitle: `${customer.visits} visits · ${S.money(customer.spend_cents)} spent${customer.no_shows ? ` · ${customer.no_shows} no-shows` : ""}`,
      body: h("div", { class: "suite-body" },
        form.node,
        h("div", { class: "btn-row" }, S.button("Save", async () => { await call(`/customers/${id}`, { method: "PATCH", body: form.read() }); Aira.toast("Saved.", "success"); rerender(); }), S.button("Book again", () => { Aira.closeDrawer(); newBooking({ customer }); }, { kind: "secondary" })),
        S.section("Packages", S.table({ columns: [{ key: "name", label: "Package" }, { key: "remaining", label: "Left", num: true }, { key: "status", label: "Status", render: (p) => S.pill(p.status) }, { key: "paid_at", label: "Paid", render: (p) => (p.paid_at ? S.day(p.paid_at) : "Unpaid") }], rows: customer.packages, empty: "No packages" }),
          pick ? h("div", { class: "btn-row" }, pick, h("label", { class: "suite-check" }, paid, h("span", { text: "Paid now" })), S.button("Sell package", async () => { await call("/customer-packages", { method: "POST", body: { customer_id: id, package_id: pick.value, paid: paid.checked } }); Aira.toast("Package sold.", "success"); Aira.closeDrawer(); openCustomer(id); })) : h("p", { class: "muted small", text: "Create packages in the Packages tab to sell them here." })),
        S.section("History", S.table({ columns: [{ key: "start_at", label: "When", render: (b) => S.when(b.start_at) }, { key: "service_name", label: "Service" }, { key: "status", label: "Status", render: (b) => status(b.status) }, { key: "paid", label: "Paid", render: paidLabel }], rows: customer.history, empty: "No bookings", onRow: (b) => { Aira.closeDrawer(); openBooking(b.id); } }))),
    });
  }

  // ------------------------------------------------------------------ packages
  async function packagesTab(body) {
    const [{ packages, sold }, { services: list }] = await Promise.all([call("/packages"), call("/services")]);
    const serviceNames = Object.fromEntries(list.map((s) => [s.id, s.name]));
    const edit = (row) => S.edit({
      title: row ? `Edit ${row.name}` : "New package",
      fields: [{ name: "name", label: "Name", required: true, placeholder: "5 massages" }, { name: "service_id", label: "For", type: "select", options: [["", "Any service"], ...list.map((s) => [s.id, s.name])] }, { name: "sessions", label: "Sessions", type: "number", min: 1, max: 500, required: true }, { name: "price_cents", label: "Price", type: "money" }, { name: "valid_days", label: "Valid for (days, 0 = no expiry)", type: "number", min: 0 }, { name: "active", label: "On sale", type: "checkbox" }],
      values: row || { sessions: 5, valid_days: 180, active: true },
      onSave: async (values) => { await call(row ? `/packages/${row.id}` : "/packages", { method: row ? "PATCH" : "POST", body: values }); rerender(); },
    });
    body.append(
      h("p", { class: "muted", text: "Sell sessions up front: customers commit to coming back, and you are paid before the work." }),
      h("div", { class: "btn-row" }, S.button("New package", () => edit(null))),
      S.table({ columns: [{ key: "name", label: "Package" }, { key: "service_id", label: "For", render: (p) => serviceNames[p.service_id] || "Any service" }, { key: "sessions", label: "Sessions", num: true }, { key: "price_cents", label: "Price", num: true, render: (p) => S.money(p.price_cents) }, { key: "sold", label: "Sold", num: true }, { key: "active", label: "", render: (p) => (p.active ? S.pill("active") : S.pill("archived")) }], rows: packages, empty: Aira.ui.empty("No packages yet", "For example: 5 sessions for the price of 4."), onRow: edit }),
      S.section("Sold", S.table({
        columns: [{ key: "customer_name", label: "Customer" }, { key: "name", label: "Package" }, { key: "remaining", label: "Left", num: true }, { key: "status", label: "Status", render: (p) => S.pill(p.status) }, { key: "expires_on", label: "Expires" }, { key: "paid", label: "Paid", render: (p) => (p.paid_at ? S.day(p.paid_at) : S.button("Mark paid", async () => { await call(`/customer-packages/${p.id}/pay`, { method: "POST", body: {} }); rerender(); }, { kind: "secondary sm" })) }],
        rows: sold,
        empty: "None sold yet (sell from a customer's page)",
      })),
    );
  }

  // ------------------------------------------------------------------ waiting list
  async function waitlist(body) {
    const { waitlist: rows } = await call("/waitlist");
    const STATUS = { waiting: "info", notified: "warn", booked: "good" };
    body.append(
      h("p", { class: "muted cm-lead", text: "When a day is fully booked, customers can join its waiting list on your booking page. If a booking that day is cancelled or moved, everyone waiting with an email gets a message (in Business hub > Outbox) and the first to book gets the time. Call the ones who left only a phone number." }),
      S.table({
        columns: [
          { key: "day", label: "Day", render: (w) => h("span", { class: "cm-stack" }, h("strong", { text: S.day(w.day) }), h("span", { class: "muted small", text: `${w.service_name || "?"}${w.staff_name ? ` · ${w.staff_name}` : ""}` })) },
          { key: "name", label: "Customer", render: (w) => h("span", { class: "cm-stack" }, h("span", { text: w.name }), w.email ? h("a", { class: "small", href: `mailto:${w.email}`, text: w.email }) : null, w.phone ? h("a", { class: "small", href: `tel:${w.phone.replace(/[^0-9+]/g, "")}`, text: w.phone }) : null) },
          { key: "status", label: "Status", render: (w) => h("span", {}, S.pill(w.status, STATUS), w.free_now && w.status !== "booked" ? h("span", {}, " ", S.pill("time free", { "time free": "good" })) : null) },
          { key: "x", label: "", render: (w) => h("div", { class: "btn-row" },
            w.status !== "booked" ? S.button("Book", () => newBooking({ day: w.day, serviceId: w.service_id, staffId: w.staff_id, customer: { name: w.name, email: w.email, phone: w.phone } }), { kind: "secondary sm" }) : null,
            S.button("Remove", async () => { await call(`/waitlist/${w.id}`, { method: "DELETE" }); Aira.toast("Removed from the waiting list.", "success"); rerender(); }, { kind: "ghost sm" })) },
        ],
        rows,
        empty: Aira.ui.empty("Nobody is waiting", "When a day is full, customers can join its waiting list from your booking page."),
      }),
    );
  }

  // ------------------------------------------------------------------ team pay
  async function teamPay(body, route) {
    const q = route.query;
    const params = new URLSearchParams({ ...(q.from ? { from: q.from } : {}), ...(q.to ? { to: q.to } : {}) });
    const data = await call(`/earnings${params.toString() ? `?${params}` : ""}`);
    const from = h("input", { type: "date", value: data.from, "aria-label": "From" });
    const to = h("input", { type: "date", value: data.to, "aria-label": "To" });
    const show = (range) => Aira.router.go(["bookings"], { tab: "pay", ...range });
    const month = (offset) => { const d = new Date(`${data.from}T12:00:00`); d.setDate(1); d.setMonth(d.getMonth() + offset); const end = new Date(d); end.setMonth(end.getMonth() + 1); end.setDate(0); return { from: isoDay(d), to: isoDay(end) }; };
    from.addEventListener("change", () => show({ from: from.value, to: to.value }));
    to.addEventListener("change", () => show({ from: from.value, to: to.value }));
    const money = (cents) => S.money(cents, data.currency);
    body.append(
      h("p", { class: "muted cm-lead", text: "What each person brought in from completed appointments, and the commission you owe them at the rate that applied on the day (set rates under Staff). A session paid from a package counts at its share of the package price." }),
      h("div", { class: "btn-row cm-filters" }, Aira.ui.field("From", from), Aira.ui.field("To", to),
        S.button("Previous month", () => show(month(-1)), { kind: "secondary sm" }),
        S.button("Next month", () => show(month(1)), { kind: "secondary sm" }),
        S.button("Download CSV", () => Aira.download(`/api/bookings/earnings?format=csv&from=${encodeURIComponent(data.from)}&to=${encodeURIComponent(data.to)}`, "team-pay.csv"), { kind: "secondary sm" })),
      S.kpis([
        { label: "Completed appointments", value: data.totals.appointments },
        { label: "Takings", value: money(data.totals.takings_cents), tone: "good" },
        { label: "Commission to pay", value: money(data.totals.commission_cents), tone: data.totals.commission_cents ? "warn" : "" },
        { label: "No-shows", value: data.totals.no_shows, tone: data.totals.no_shows ? "bad" : "" },
      ]),
      S.table({
        columns: [
          { key: "name", label: "Person", render: (p) => h("span", {}, h("span", { class: "cm-dot", style: { background: p.color || "var(--brand)" } }), p.name) },
          { key: "appointments", label: "Done", num: true },
          { key: "no_shows", label: "No-shows", num: true },
          { key: "takings_cents", label: "Takings", num: true, render: (p) => money(p.takings_cents) },
          { key: "commission_percent", label: "Rate", num: true, render: (p) => ((p.rates || []).length ? p.rates : [p.commission_percent]).map((rate) => `${rate}%`).join(", then ") },
          { key: "commission_cents", label: "Commission", num: true, render: (p) => money(p.commission_cents) },
        ],
        rows: data.team,
        empty: Aira.ui.empty("Nothing completed in these days", "Mark appointments completed (and record payments) to see takings and commission here."),
      }),
    );
  }

  // ------------------------------------------------------------------ settings
  async function settings(body) {
    const data = await call("/settings");
    const form = S.form([
      { name: "slot_interval", label: "Start times every (minutes)", type: "number", min: 5, max: 240 },
      { name: "min_notice_hours", label: "Minimum notice (hours)", type: "number", min: 0, hint: "How soon before an appointment customers may book." },
      { name: "max_days_ahead", label: "Book up to (days ahead)", type: "number", min: 1, max: 365 },
      { name: "cancel_notice_hours", label: "Customers may cancel or move until (hours before)", type: "number", min: 0 },
      { name: "timezone", label: "Time zone", placeholder: data.timezone, hint: "Empty uses the Business hub profile. A name like Europe/London or America/New_York." },
      { name: "auto_confirm", label: "Confirm online bookings automatically", type: "checkbox", hint: "Off: online bookings wait for you to confirm them." },
      { name: "max_active_per_customer", label: "Upcoming bookings one customer may hold", type: "number", min: 1, max: 50 },
      { name: "review_link", label: "Review link", type: "url", placeholder: "https://g.page/r/...", hint: "After a completed appointment, Monosage prepares a short review request." },
      { name: "welcome", label: "Welcome line on the booking page", placeholder: "New clients: please arrive 10 minutes early." },
    ], data.rules);
    body.append(
      S.section("Booking rules", form.node, h("div", { class: "btn-row" }, S.button("Save", async () => { form.setError(""); try { await call("/settings", { method: "POST", body: form.read() }); Aira.toast("Saved.", "success"); } catch (error) { form.setError(error.message); } }))),
      S.section("Messages to customers", h("p", { class: "muted", text: "Confirmations, reminders a day before, cancellations and review requests wait in Business hub > Outbox. To send confirmations and reminders without approving each one, add booking_confirmation, booking_reminder under Business hub > Email." }), h("a", { class: "btn secondary sm", href: "#/hub?tab=outbox", text: "Open the outbox" })),
    );
  }

  // The left menu shows the Bookings folders from the start, before the page is first opened.
  if (Aira.shell) Aira.shell.tabs("bookings", TABS, null);

  Aira.views.bookings = async (main, route) => {
    // A once-link (#/bookings?tab=appointments&booking=<id>): remember it, take it out of the address so Back and
    // refresh do not open it again, and let the redrawn tab open it.
    if (ONCE.some((key) => route.query[key])) {
      pending = Object.fromEntries(ONCE.filter((key) => route.query[key]).map((key) => [key, route.query[key]]));
      Aira.router.go(["bookings"], Object.fromEntries(Object.entries(route.query).filter(([key]) => !ONCE.includes(key))), { replace: true });
      return;
    }
    await S.business();
    // On a wide screen "+ New" above the menu books; a phone keeps the button in the page head (cm-head-new).
    const create = !Aira.commerce || !Aira.commerce.canCreate || Aira.commerce.canCreate();
    const body = S.page(main, { title: "Bookings", subtitle: "Appointments that pay: your booking page, calendar, customers and packages.", actions: [create ? S.button("New booking", () => newBooking({}), { kind: "cm-head-new", icon: "plus" }) : null] });
    if (create) body.classList.add("cm-has-head-new"); // the tabs' own "New booking" then waits for a wide screen
    const tab = S.tabs(body, route, "bookings", TABS);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    rerender = async () => {
      if (!document.body.contains(panel)) return;
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "calendar") await calendar(holder, route);
        else if (tab === "appointments") await appointments(holder, route);
        else if (tab === "waitlist") await waitlist(holder);
        else if (tab === "pay") await teamPay(holder, route);
        else if (tab === "services") await services(holder);
        else if (tab === "staff") await staffTab(holder);
        else if (tab === "customers") await customers(holder, route);
        else if (tab === "packages") await packagesTab(holder);
        else if (tab === "settings") await settings(holder);
        else await overview(holder);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, rerender)); }
      pending = {}; // a once-link for another tab, or a record that failed to load, does not linger
      if (Aira.commerce && Aira.commerce.forget) Aira.commerce.forget(); // a change shows in the next search
    };
    panel.replaceChildren(Aira.ui.skeleton(4));
    await rerender();
  };
})();

"""Bookings routes (``/api/bookings/*``), its public pages (``booking_page``, ``booking``) and its hub numbers.

Loaded by :func:`aira.suite.load_apps`. Every handler opens the account's database (the person's own locally,
the organization's shared one hosted) and creates the Bookings tables on first use.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, public
from aira.suite.bookings import service, store
from aira.suite.bookings.store import APP

BASE = "/api/bookings"
ID = "([0-9a-f]{32})"


def _open(request: Request) -> Any:
    connection = request.connect()
    store.ensure(connection)
    return connection


def _actor(request: Request) -> str:
    return str((request.user or {}).get("email") or "")


# ---------------------------------------------------------------------------
# overview, rules, the booking page link
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, service.overview(connection)


@aira_route("GET", f"{BASE}/settings")
def settings_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "rules": store.get_rules(connection),
            "timezone": store.zone_name(connection),
        }


@aira_route("POST", f"{BASE}/settings")
def settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        rules = store.save_rules(connection, request.payload)
        kit.log(
            connection, APP, "settings", "Booking rules updated", actor=_actor(request)
        )
        connection.commit()
        timezone = store.zone_name(connection)
    request.audit("bookings.settings", "bookings", "rules")
    return 200, {"rules": rules, "timezone": timezone}


@aira_route("GET", f"{BASE}/page-link")
def page_link_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "link": public.link_for(
                connection, APP, service.PAGE_KIND, service.PAGE_REF
            )
        }


@aira_route("POST", f"{BASE}/page-link")
def page_link_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        name = kit.business(connection)["name"] or "Book an appointment"
        link = public.create_link(
            connection,
            APP,
            service.PAGE_KIND,
            service.PAGE_REF,
            title=f"Book with {name}",
        )
        kit.log(
            connection,
            APP,
            "shared",
            "Booking page link created",
            actor=_actor(request),
        )
        connection.commit()
    request.audit("bookings.page_link", "bookings", "page")
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/page-link")
def page_link_revoke(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        count = public.revoke(connection, APP, service.PAGE_KIND, service.PAGE_REF)
    request.audit("bookings.page_link_revoke", "bookings", "page")
    return 200, {"revoked": count}


# ---------------------------------------------------------------------------
# services
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/services")
def services_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {"services": store.list_services(connection)}


@aira_route("POST", f"{BASE}/services")
def services_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        created = store.create_service(connection, request.payload)
        kit.log(
            connection,
            APP,
            "service",
            f"Service added: {created['name']}",
            ref_type="service",
            ref_id=created["id"],
            actor=_actor(request),
        )
        connection.commit()
    request.audit("bookings.service_create", "service", created["id"])
    return 201, {"service": created}


@aira_route("PATCH", f"{BASE}/services/{ID}")
def services_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        updated = store.update_service(
            connection, request.match.group(1), request.payload
        )
    request.audit("bookings.service_update", "service", updated["id"])
    return 200, {"service": updated}


@aira_route("DELETE", f"{BASE}/services/{ID}")
def services_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        outcome = store.delete_service(connection, request.match.group(1))
    request.audit("bookings.service_delete", "service", request.match.group(1))
    return 200, outcome


# ---------------------------------------------------------------------------
# staff, hours, time off
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/staff")
def staff_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "staff": store.list_staff(connection),
            "weekdays": list(store.WEEKDAYS),
        }


@aira_route("POST", f"{BASE}/staff")
def staff_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        created = store.create_staff(connection, request.payload)
    request.audit("bookings.staff_create", "staff", created["id"])
    return 201, {"person": created}


@aira_route("PATCH", f"{BASE}/staff/{ID}")
def staff_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        updated = store.update_staff(
            connection, request.match.group(1), request.payload
        )
    request.audit("bookings.staff_update", "staff", updated["id"])
    return 200, {"person": updated}


@aira_route("DELETE", f"{BASE}/staff/{ID}")
def staff_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        outcome = store.delete_staff(connection, request.match.group(1))
    request.audit("bookings.staff_delete", "staff", request.match.group(1))
    return 200, outcome


@aira_route("POST", f"{BASE}/staff/{ID}/hours")
def staff_hours(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        hours = store.set_hours(
            connection, request.match.group(1), request.payload.get("hours")
        )
    request.audit("bookings.hours", "staff", request.match.group(1))
    return 200, {"hours": hours}


@aira_route("GET", f"{BASE}/time-off")
def time_off_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "time_off": store.list_time_off(connection, from_day=request.q("from"))
        }


@aira_route("POST", f"{BASE}/time-off")
def time_off_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        entry = store.add_time_off(connection, request.payload)
    request.audit("bookings.time_off", "time_off", entry["id"])
    return 201, {"time_off": entry}


@aira_route("DELETE", f"{BASE}/time-off/{ID}")
def time_off_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        store.delete_time_off(connection, request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# slots, bookings, agenda
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/slots")
def slots(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        days = service.slots_for_days(
            connection,
            request.q("service_id"),
            request.q("day") or kit.today(),
            request.qint("days", 1, 1, 14),
            staff_id=request.q("staff_id"),
            customer_rules=request.q("customer_rules") == "1",
            exclude_booking=request.q(
                "exclude"
            ),  # moving a booking: its own time is free
        )
        return 200, {"days": days, "timezone": store.zone_name(connection)}


@aira_route("GET", f"{BASE}/bookings")
def bookings_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as connection:
        if request.q("format") == "csv":
            return Raw(
                200,
                service.bookings_csv(connection),
                "text/csv; charset=utf-8",
                filename="bookings.csv",
            )
        rows = service.list_bookings(
            connection,
            when=kit.choice(
                request.q("when"),
                "when",
                ("upcoming", "past", "all"),
                default="upcoming",
            ),
            status=request.q("status"),
            q=request.q("q"),
            staff_id=request.q("staff_id"),
            customer_id=request.q("customer_id"),
            from_day=request.q("from"),
            to_day=request.q("to"),
            limit=request.qint("limit", 300, 1, 2000),
        )
        return 200, {"bookings": rows, "timezone": store.zone_name(connection)}


@aira_route("POST", f"{BASE}/bookings")
def bookings_create(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _open(request) as connection:
        start = str(payload.get("start") or "")
        if not start and payload.get("day") and payload.get("time"):
            # A day and a wall-clock time in the business's time zone (a phone or walk-in booking).
            day = date.fromisoformat(
                kit.day(payload.get("day"), "the day", required=True)
            )
            start = kit.iso(
                service.local_at(
                    day,
                    store.hhmm(payload.get("time"), "the time"),
                    store.zone(connection),
                )
            )
        booking = service.book(
            connection,
            service_id=str(payload.get("service_id") or ""),
            start=start,
            staff_id=str(payload.get("staff_id") or ""),
            customer={
                "name": payload.get("name"),
                "email": payload.get("email"),
                "phone": payload.get("phone"),
            },
            note=str(payload.get("note") or ""),
            internal_note=str(payload.get("internal_note") or ""),
            source="manual",
            status=str(payload.get("status") or ""),
            outside_hours=kit.flag(payload.get("outside_hours")),
            origin=request,
        )
    request.audit("bookings.create", "booking", booking["id"])
    return 201, {"booking": booking}


@aira_route("GET", f"{BASE}/bookings/{ID}")
def bookings_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        booking = service.get_booking(connection, request.match.group(1))
        booking["link"] = public.link_for(
            connection, APP, service.BOOKING_KIND, booking["id"]
        )
        booking["packages"] = [
            item
            for item in service.list_customer_packages(
                connection, customer_id=booking["customer_id"]
            )
            if item["status"] == "active" and item["remaining"] > 0
        ]
        return 200, {"booking": booking}


@aira_route("PATCH", f"{BASE}/bookings/{ID}")
def bookings_update(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _open(request) as connection:
        booking_id = request.match.group(1)
        if payload.get("status"):
            if payload["status"] == "cancelled":
                booking = service.cancel(
                    connection, booking_id, reason=str(payload.get("reason") or "")
                )
            else:
                booking = service.set_status(
                    connection, booking_id, str(payload["status"])
                )
        if "note" in payload or "internal_note" in payload:
            booking = service.update_notes(connection, booking_id, payload)
        booking = service.get_booking(connection, booking_id)
    request.audit(
        "bookings.update", "booking", booking_id, {"status": payload.get("status", "")}
    )
    return 200, {"booking": booking}


@aira_route("POST", f"{BASE}/bookings/{ID}/cancel")
def bookings_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        booking = service.cancel(
            connection,
            request.match.group(1),
            reason=str(request.payload.get("reason") or ""),
        )
    request.audit("bookings.cancel", "booking", booking["id"])
    return 200, {"booking": booking}


@aira_route("POST", f"{BASE}/bookings/{ID}/reschedule")
def bookings_reschedule(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        booking = service.reschedule(
            connection,
            request.match.group(1),
            str(request.payload.get("start") or ""),
            staff_id=str(request.payload.get("staff_id") or ""),
            origin=request,
        )
    request.audit("bookings.reschedule", "booking", booking["id"])
    return 200, {"booking": booking}


@aira_route("POST", f"{BASE}/bookings/{ID}/pay")
def bookings_pay(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        booking = service.mark_paid(
            connection,
            request.match.group(1),
            kind=str(request.payload.get("kind") or "full"),
            amount_cents=request.payload.get("amount_cents"),
        )
    request.audit(
        "bookings.pay", "booking", booking["id"], {"paid_cents": booking["paid_cents"]}
    )
    return 200, {"booking": booking}


@aira_route("POST", f"{BASE}/bookings/{ID}/unpay")
def bookings_unpay(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        booking = service.mark_unpaid(connection, request.match.group(1))
    request.audit("bookings.unpay", "booking", booking["id"])
    return 200, {"booking": booking}


@aira_route("POST", f"{BASE}/bookings/{ID}/redeem")
def bookings_redeem(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        booking = service.redeem(
            connection,
            request.match.group(1),
            str(request.payload.get("customer_package_id") or ""),
        )
    request.audit("bookings.redeem", "booking", booking["id"])
    return 200, {"booking": booking}


@aira_route("POST", f"{BASE}/bookings/{ID}/link")
def bookings_link(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        link = service.booking_link(connection, request.match.group(1))
        connection.commit()
    return 200, {"link": link}


@aira_route("GET", f"{BASE}/agenda")
def agenda(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, service.agenda(
            connection,
            request.q("start") or kit.today(),
            request.qint("days", 7, 1, 31),
            staff_id=request.q("staff_id"),
        )


# ---------------------------------------------------------------------------
# customers and packages
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/customers")
def customers_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as connection:
        if request.q("format") == "csv":
            return Raw(
                200,
                service.customers_csv(connection),
                "text/csv; charset=utf-8",
                filename="customers.csv",
            )
        return 200, {"customers": service.list_customers(connection, q=request.q("q"))}


@aira_route("GET", f"{BASE}/customers/{ID}")
def customers_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "customer": service.get_customer(connection, request.match.group(1))
        }


@aira_route("PATCH", f"{BASE}/customers/{ID}")
def customers_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        customer = service.update_customer(
            connection, request.match.group(1), request.payload
        )
    request.audit("bookings.customer_update", "customer", customer["id"])
    return 200, {"customer": customer}


@aira_route("GET", f"{BASE}/packages")
def packages_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "packages": store.list_packages(connection),
            "sold": service.list_customer_packages(
                connection, customer_id=request.q("customer_id")
            ),
        }


@aira_route("POST", f"{BASE}/packages")
def packages_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        created = store.create_package(connection, request.payload)
    request.audit("bookings.package_create", "package", created["id"])
    return 201, {"package": created}


@aira_route("PATCH", f"{BASE}/packages/{ID}")
def packages_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        updated = store.update_package(
            connection, request.match.group(1), request.payload
        )
    return 200, {"package": updated}


@aira_route("DELETE", f"{BASE}/packages/{ID}")
def packages_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        outcome = store.delete_package(connection, request.match.group(1))
    return 200, outcome


@aira_route("POST", f"{BASE}/customer-packages")
def customer_packages_sell(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _open(request) as connection:
        sold = service.sell_package(
            connection,
            customer_id=str(payload.get("customer_id") or ""),
            package_id=str(payload.get("package_id") or ""),
            paid=kit.flag(payload.get("paid")),
        )
    request.audit("bookings.package_sell", "customer_package", sold["id"])
    return 201, {"package": sold}


@aira_route("POST", f"{BASE}/customer-packages/{ID}/pay")
def customer_packages_pay(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        sold = service.pay_package(connection, request.match.group(1))
    request.audit("bookings.package_pay", "customer_package", sold["id"])
    return 200, {"package": sold}


@aira_route("POST", f"{BASE}/customer-packages/{ID}/cancel")
def customer_packages_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        sold = service.cancel_package(
            connection,
            request.match.group(1),
            refund=kit.flag(request.payload.get("refund")),
        )
    request.audit("bookings.package_cancel", "customer_package", sold["id"])
    return 200, {"package": sold}


# ---------------------------------------------------------------------------
# public pages
# ---------------------------------------------------------------------------


@public.page(APP, service.PAGE_KIND)
def booking_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    return service.public_page(connection)


@public.action(APP, service.PAGE_KIND, "slots")
def booking_page_slots(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    service_id = str(payload.get("service_id") or "")
    if not any(
        item["id"] == service_id for item in service.public_page(connection)["services"]
    ):
        raise LookupError("That service is not available to book.")
    days = service.slots_for_days(
        connection,
        service_id,
        str(payload.get("day") or kit.today()),
        kit.integer(
            payload.get("days"), "the number of days", default=7, minimum=1, maximum=14
        ),
        staff_id=str(payload.get("staff_id") or ""),
    )
    return 200, {
        "days": [
            {
                "day": d["day"],
                "slots": [
                    {"start": s["start"], "local": s["local"]} for s in d["slots"]
                ],
            }
            for d in days
        ]
    }


@public.action(APP, service.PAGE_KIND, "book")
def booking_page_book(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    service_id = str(payload.get("service_id") or "")
    if not any(
        item["id"] == service_id for item in service.public_page(connection)["services"]
    ):
        raise LookupError("That service is not available to book.")
    booking = service.book(
        connection,
        service_id=service_id,
        start=str(payload.get("start") or ""),
        staff_id=str(payload.get("staff_id") or ""),
        customer={
            "name": payload.get("name"),
            "email": payload.get("email"),
            "phone": payload.get("phone"),
        },
        note=str(payload.get("note") or ""),
        source="public",
        origin=visitor,
    )
    return 201, {
        "booking": service.public_booking(connection, booking),
        "manage_path": booking["manage_path"],
    }


def _own_booking(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    try:
        return service.get_booking(connection, link["ref_id"])
    except LookupError:
        raise LookupError("This booking is no longer available.") from None


@public.page(APP, service.BOOKING_KIND)
def booking_view(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    booking = _own_booking(connection, link)
    rules = store.get_rules(connection)
    return {
        "booking": service.public_booking(connection, booking),
        "timezone": store.zone_name(connection),
        "cancel_notice_hours": int(rules["cancel_notice_hours"]),
        "today": kit.today(),
    }


@public.action(APP, service.BOOKING_KIND, "slots")
def booking_slots(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    booking = _own_booking(connection, link)
    if not service.customer_may_change(connection, booking):
        raise ValueError(
            "This booking can no longer be changed online. Please contact the business."
        )
    days = service.slots_for_days(
        connection,
        booking["service_id"],
        str(payload.get("day") or kit.today()),
        kit.integer(
            payload.get("days"), "the number of days", default=7, minimum=1, maximum=14
        ),
        exclude_booking=booking["id"],
    )
    return 200, {
        "days": [
            {
                "day": d["day"],
                "slots": [
                    {"start": s["start"], "local": s["local"]} for s in d["slots"]
                ],
            }
            for d in days
        ]
    }


@public.action(APP, service.BOOKING_KIND, "cancel")
def booking_cancel(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    booking = _own_booking(connection, link)
    updated = service.cancel(
        connection,
        booking["id"],
        by="customer",
        reason=str(payload.get("reason") or ""),
    )
    return 200, {"booking": service.public_booking(connection, updated)}


@public.action(APP, service.BOOKING_KIND, "reschedule")
def booking_reschedule(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    booking = _own_booking(connection, link)
    updated = service.reschedule(
        connection,
        booking["id"],
        str(payload.get("start") or ""),
        by="customer",
        origin=visitor,
    )
    return 200, {"booking": service.public_booking(connection, updated)}


# ---------------------------------------------------------------------------
# the Business hub
# ---------------------------------------------------------------------------


@kit.checklist(APP)
def _checklist(connection: Any) -> list[dict[str, Any]]:
    return service.checklist(connection)


@kit.summary(APP)
def _summary(connection: Any) -> dict[str, Any]:
    store.ensure(connection)
    return service.summary(connection)

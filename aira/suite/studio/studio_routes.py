"""Studio's routes (``/api/studio/*``), its public pages (a proposal to accept, an invoice to pay) and its hub
checklist and summary. Loaded by :func:`aira.suite.load_apps`. Domain rules live in :mod:`.service`."""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, public
from aira.suite.studio import sales, service, store

BASE = "/api/studio"
ID = "([0-9a-f]{32})"
APP = service.APP


def _today() -> str:
    return kit.today()


def _absolute(request: Request, path: str) -> str:
    """The full address of a public page for an email (the hosted build's public address wins)."""
    return kit.public_url(path, request)


# ---------------------------------------------------------------------------
# overview and settings
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        data = service.overview(connection, today=_today())
        data["checklist"] = service.checklist(connection)
        data["settings"] = service.settings(connection)
        return 200, data


@aira_route("GET", f"{BASE}/settings")
def settings_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {"settings": service.settings(connection)}


@aira_route("POST", f"{BASE}/settings")
def settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        saved = service.save_settings(connection, request.payload)
    request.audit("studio.settings", "studio", "settings")
    return 200, {"settings": saved}


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/clients")
def clients_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "clients": service.list_clients(
                connection,
                q=request.q("q"),
                status=request.q("status"),
                tag=request.q("tag"),
            )
        }


@aira_route("GET", f"{BASE}/clients/export")
def clients_export(request: Request) -> Raw:
    with request.connect() as connection:
        store.ensure(connection)
        rows = service.list_clients(
            connection,
            q=request.q("q"),
            status=request.q("status"),
            tag=request.q("tag"),
        )
    for row in rows:
        row["tags"] = ", ".join(row["tags"])
        row["billed"] = f"{row['billed_cents'] / 100:.2f}"
        row["paid"] = f"{row['paid_cents'] / 100:.2f}"
        row["outstanding"] = f"{row['outstanding_cents'] / 100:.2f}"
    columns = (
        "name",
        "company",
        "email",
        "phone",
        "status",
        "tags",
        "billed",
        "paid",
        "outstanding",
        "notes",
        "created_at",
    )
    return Raw(
        200,
        kit.csv_bytes(rows, columns),
        "text/csv; charset=utf-8",
        filename="studio-clients.csv",
    )


@aira_route("POST", f"{BASE}/clients")
def clients_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        client = service.create_client(connection, request.payload)
    request.audit("studio.client_create", "studio_client", client["id"])
    return 201, {"client": client}


@aira_route("GET", f"{BASE}/clients/{ID}")
def clients_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, service.client_detail(connection, request.match.group(1), _today())


@aira_route("PATCH", f"{BASE}/clients/{ID}")
def clients_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        client = service.update_client(
            connection, request.match.group(1), request.payload
        )
    request.audit("studio.client_update", "studio_client", client["id"])
    return 200, {"client": client}


@aira_route("DELETE", f"{BASE}/clients/{ID}")
def clients_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.delete_client(connection, request.match.group(1))
    request.audit("studio.client_delete", "studio_client", request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# proposals
# ---------------------------------------------------------------------------


def _proposal_out(connection: Any, proposal_id: str) -> dict[str, Any]:
    return {
        "proposal": service.get_proposal(connection, proposal_id, _today()),
        "link": public.link_for(connection, APP, "proposal", proposal_id),
    }


@aira_route("GET", f"{BASE}/proposals")
def proposals_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "proposals": service.list_proposals(
                connection,
                today=_today(),
                status=request.q("status"),
                client_id=request.q("client_id"),
            )
        }


@aira_route("POST", f"{BASE}/proposals")
def proposals_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        proposal = service.create_proposal(connection, request.payload, today=_today())
        out = _proposal_out(connection, proposal["id"])
    request.audit("studio.proposal_create", "studio_proposal", proposal["id"])
    return 201, out


@aira_route("POST", f"{BASE}/proposals/draft")
def proposals_draft(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "draft": service.draft_proposal(
                connection,
                str(payload.get("brief") or ""),
                client_id=kit.text(payload.get("client_id"), "a client", max_len=64),
                title=str(payload.get("title") or ""),
            )
        }


@aira_route("GET", f"{BASE}/proposals/{ID}")
def proposals_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, _proposal_out(connection, request.match.group(1))


@aira_route("PATCH", f"{BASE}/proposals/{ID}")
def proposals_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.update_proposal(
            connection, request.match.group(1), request.payload, today=_today()
        )
        out = _proposal_out(connection, request.match.group(1))
    request.audit("studio.proposal_update", "studio_proposal", request.match.group(1))
    return 200, out


@aira_route("DELETE", f"{BASE}/proposals/{ID}")
def proposals_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.delete_proposal(connection, request.match.group(1))
    request.audit("studio.proposal_delete", "studio_proposal", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/proposals/{ID}/status")
def proposals_status(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.set_proposal_status(
            connection,
            request.match.group(1),
            str(request.payload.get("status") or ""),
            today=_today(),
        )
        out = _proposal_out(connection, request.match.group(1))
    request.audit(
        "studio.proposal_status",
        "studio_proposal",
        request.match.group(1),
        {"status": out["proposal"]["status"]},
    )
    return 200, out


def _proposal_link(connection: Any, proposal_id: str) -> dict[str, Any]:
    proposal = service.get_proposal(connection, proposal_id, _today())
    if proposal["status"] == "declined":
        raise ValueError("This proposal was declined. Make a new one to share.")
    link = public.create_link(
        connection,
        APP,
        "proposal",
        proposal_id,
        title=f"Proposal {proposal['number']}: {proposal['title']}",
    )
    service.mark_proposal_sent(connection, proposal_id)
    return link


@aira_route("POST", f"{BASE}/proposals/{ID}/link")
def proposals_link(request: Request) -> tuple[int, dict[str, Any]]:
    """Sharing a proposal's page sends it: a draft becomes sent and can be accepted from the link."""
    with request.connect() as connection:
        store.ensure(connection)
        link = _proposal_link(connection, request.match.group(1))
        out = _proposal_out(connection, request.match.group(1))
    return 200, {**out, "link": link}


@aira_route("DELETE", f"{BASE}/proposals/{ID}/link")
def proposals_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        store.get(connection, "st_proposals", request.match.group(1), "proposal")
        public.revoke(connection, APP, "proposal", request.match.group(1))
        return 200, _proposal_out(connection, request.match.group(1))


@aira_route("POST", f"{BASE}/proposals/{ID}/send")
def proposals_send(request: Request) -> tuple[int, dict[str, Any]]:
    """Share the proposal and put an email with its link in the outbox (approved there, or sent automatically)."""
    proposal_id = request.match.group(1)
    with request.connect() as connection:
        store.ensure(connection)
        link = _proposal_link(connection, proposal_id)
        proposal = service.get_proposal(connection, proposal_id, _today())
        to = kit.email(request.payload.get("email")) or proposal["client_email"]
        message = None
        if to:
            subject, body = service.proposal_email(
                connection, proposal, _absolute(request, link["path"])
            )
            message = kit.queue_email(
                connection,
                APP,
                "proposal",
                to=to,
                subject=subject,
                body=body,
                ref_type="proposal",
                ref_id=proposal_id,
            )
            connection.commit()
        out = _proposal_out(connection, proposal_id)
    request.audit("studio.proposal_send", "studio_proposal", proposal_id)
    return 200, {
        **out,
        "message": message,
        "note": ""
        if message
        else "The client has no email address: copy the link and send it yourself.",
    }


@aira_route("POST", f"{BASE}/proposals/{ID}/convert")
def proposals_convert(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        result = service.convert_proposal(
            connection, request.match.group(1), today=_today()
        )
    request.audit("studio.proposal_convert", "studio_proposal", request.match.group(1))
    return 200, result


# ---------------------------------------------------------------------------
# projects, tasks, time
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/projects")
def projects_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "projects": service.list_projects(
                connection, status=request.q("status"), client_id=request.q("client_id")
            )
        }


@aira_route("POST", f"{BASE}/projects")
def projects_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        project = service.create_project(connection, request.payload)
    request.audit("studio.project_create", "studio_project", project["id"])
    return 201, {"project": project}


@aira_route("GET", f"{BASE}/projects/{ID}")
def projects_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, service.project_detail(connection, request.match.group(1), _today())


@aira_route("PATCH", f"{BASE}/projects/{ID}")
def projects_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        project = service.update_project(
            connection, request.match.group(1), request.payload
        )
    return 200, {"project": project}


@aira_route("DELETE", f"{BASE}/projects/{ID}")
def projects_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.delete_project(connection, request.match.group(1))
    request.audit("studio.project_delete", "studio_project", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/projects/{ID}/tasks")
def tasks_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 201, {
            "task": service.add_task(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("PATCH", f"{BASE}/tasks/{ID}")
def tasks_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "task": service.update_task(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/tasks/{ID}")
def tasks_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.delete_task(connection, request.match.group(1))
        return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/projects/{ID}/time")
def time_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 201, {
            "entry": service.add_time(
                connection, request.match.group(1), request.payload, today=_today()
            )
        }


@aira_route("DELETE", f"{BASE}/time/{ID}")
def time_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.delete_time(connection, request.match.group(1))
        return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/projects/{ID}/invoice-time")
def projects_invoice_time(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        invoice = service.invoice_unbilled_time(
            connection, request.match.group(1), today=_today()
        )
    request.audit("studio.invoice_time", "studio_invoice", invoice["id"])
    return 201, {"invoice": invoice}


# ---------------------------------------------------------------------------
# invoices
# ---------------------------------------------------------------------------


def _invoice_out(connection: Any, invoice_id: str) -> dict[str, Any]:
    return {
        "invoice": service.get_invoice(connection, invoice_id, _today()),
        "link": public.link_for(connection, APP, "invoice", invoice_id),
    }


@aira_route("GET", f"{BASE}/invoices")
def invoices_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "invoices": service.list_invoices(
                connection,
                today=_today(),
                status=request.q("status"),
                client_id=request.q("client_id"),
                q=request.q("q"),
            )
        }


@aira_route("GET", f"{BASE}/invoices/export")
def invoices_export(request: Request) -> Raw:
    with request.connect() as connection:
        store.ensure(connection)
        rows = service.list_invoices(
            connection,
            today=_today(),
            status=request.q("status"),
            client_id=request.q("client_id"),
        )
    for row in rows:
        for key in ("total_cents", "paid_cents", "balance_cents", "tax_cents"):
            row[key.removesuffix("_cents")] = f"{int(row[key]) / 100:.2f}"
    columns = (
        "number",
        "client_name",
        "client_company",
        "issue_date",
        "due_date",
        "status",
        "total",
        "tax",
        "paid",
        "balance",
    )
    return Raw(
        200,
        kit.csv_bytes(rows, columns),
        "text/csv; charset=utf-8",
        filename="studio-invoices.csv",
    )


@aira_route("POST", f"{BASE}/invoices")
def invoices_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        invoice = service.create_invoice(connection, request.payload, today=_today())
        out = _invoice_out(connection, invoice["id"])
    request.audit("studio.invoice_create", "studio_invoice", invoice["id"])
    return 201, out


@aira_route("GET", f"{BASE}/invoices/{ID}")
def invoices_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, _invoice_out(connection, request.match.group(1))


@aira_route("PATCH", f"{BASE}/invoices/{ID}")
def invoices_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.update_invoice(
            connection, request.match.group(1), request.payload, today=_today()
        )
        out = _invoice_out(connection, request.match.group(1))
    request.audit("studio.invoice_update", "studio_invoice", request.match.group(1))
    return 200, out


@aira_route("DELETE", f"{BASE}/invoices/{ID}")
def invoices_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.delete_invoice(connection, request.match.group(1))
    request.audit("studio.invoice_delete", "studio_invoice", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/invoices/{ID}/void")
def invoices_void(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.void_invoice(connection, request.match.group(1), today=_today())
        out = _invoice_out(connection, request.match.group(1))
    request.audit("studio.invoice_void", "studio_invoice", request.match.group(1))
    return 200, out


def _invoice_link(connection: Any, invoice_id: str) -> dict[str, Any]:
    invoice = service.get_invoice(connection, invoice_id, _today())
    if invoice["status"] == "void":
        raise ValueError("A void invoice cannot be shared.")
    link = public.create_link(
        connection, APP, "invoice", invoice_id, title=f"Invoice {invoice['number']}"
    )
    service.mark_sent(connection, invoice_id)
    return link


@aira_route("POST", f"{BASE}/invoices/{ID}/link")
def invoices_link(request: Request) -> tuple[int, dict[str, Any]]:
    """Sharing an invoice's page issues it: a draft becomes sent."""
    with request.connect() as connection:
        store.ensure(connection)
        link = _invoice_link(connection, request.match.group(1))
        out = _invoice_out(connection, request.match.group(1))
    return 200, {**out, "link": link}


@aira_route("DELETE", f"{BASE}/invoices/{ID}/link")
def invoices_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        store.get(connection, "st_invoices", request.match.group(1), "invoice")
        public.revoke(connection, APP, "invoice", request.match.group(1))
        return 200, _invoice_out(connection, request.match.group(1))


def _queue_invoice_email(
    request: Request, *, reminder: bool
) -> tuple[int, dict[str, Any]]:
    invoice_id = request.match.group(1)
    with request.connect() as connection:
        store.ensure(connection)
        if reminder:
            current = service.get_invoice(connection, invoice_id, _today())
            if current["status"] not in ("sent", "partially_paid", "overdue"):
                raise ValueError(
                    "Only an unpaid invoice that was sent can get a reminder."
                )
        link = _invoice_link(connection, invoice_id)
        invoice = service.get_invoice(connection, invoice_id, _today())
        to = kit.email(request.payload.get("email")) or invoice["client_email"]
        message = None
        if to:
            subject, body = service.invoice_email(
                connection, invoice, _absolute(request, link["path"]), reminder=reminder
            )
            kind = "invoice_reminder" if reminder else "invoice"
            message = kit.queue_email(
                connection,
                APP,
                kind,
                to=to,
                subject=subject,
                body=body,
                ref_type="invoice",
                ref_id=invoice_id,
            )
            if reminder:
                kit.update_row(
                    connection, "st_invoices", invoice_id, {"reminded_at": kit.now()}
                )
            connection.commit()
        out = _invoice_out(connection, invoice_id)
    request.audit(
        "studio.invoice_remind" if reminder else "studio.invoice_send",
        "studio_invoice",
        invoice_id,
    )
    return 200, {
        **out,
        "message": message,
        "note": ""
        if message
        else "The client has no email address: copy the link and send it yourself.",
    }


@aira_route("POST", f"{BASE}/invoices/{ID}/send")
def invoices_send(request: Request) -> tuple[int, dict[str, Any]]:
    return _queue_invoice_email(request, reminder=False)


@aira_route("POST", f"{BASE}/invoices/{ID}/remind")
def invoices_remind(request: Request) -> tuple[int, dict[str, Any]]:
    return _queue_invoice_email(request, reminder=True)


@aira_route("POST", f"{BASE}/invoices/{ID}/payments")
def payments_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.add_payment(
            connection, request.match.group(1), request.payload, today=_today()
        )
        out = _invoice_out(connection, request.match.group(1))
    request.audit("studio.payment_add", "studio_invoice", request.match.group(1))
    return 201, out


@aira_route("DELETE", f"{BASE}/invoices/{ID}/payments/{ID}")
def payments_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.delete_payment(
            connection, request.match.group(1), request.match.group(2), today=_today()
        )
        out = _invoice_out(connection, request.match.group(1))
    request.audit("studio.payment_delete", "studio_invoice", request.match.group(1))
    return 200, out


# ---------------------------------------------------------------------------
# retainers
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/retainers")
def retainers_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {"retainers": service.list_retainers(connection)}


@aira_route("POST", f"{BASE}/retainers")
def retainers_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        retainer = service.create_retainer(connection, request.payload, today=_today())
    request.audit("studio.retainer_create", "studio_retainer", retainer["id"])
    return 201, {"retainer": retainer}


@aira_route("PATCH", f"{BASE}/retainers/{ID}")
def retainers_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "retainer": service.update_retainer(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/retainers/{ID}")
def retainers_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        service.delete_retainer(connection, request.match.group(1))
    request.audit("studio.retainer_delete", "studio_retainer", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/retainers/run")
def retainers_run(request: Request) -> tuple[int, dict[str, Any]]:
    """Make the invoices that retainers owe up to today (safe to run any number of times)."""
    with request.connect() as connection:
        store.ensure(connection)
        created = service.run_retainers(connection, today=_today())
    if created:
        request.audit(
            "studio.retainers_run", "studio", "retainers", {"invoices": len(created)}
        )
    return 200, {"created": created}


# ---------------------------------------------------------------------------
# sales: contacts, deals, activities, the forecast, leads
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/clients/{ID}/contacts")
def contacts_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        store.get(connection, "st_clients", request.match.group(1), "client")
        return 200, {
            "contacts": sales.list_contacts(connection, request.match.group(1))
        }


@aira_route("POST", f"{BASE}/clients/{ID}/contacts")
def contacts_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        contact = sales.add_contact(connection, request.match.group(1), request.payload)
    request.audit("studio.contact_create", "studio_contact", contact["id"])
    return 201, {"contact": contact}


@aira_route("PATCH", f"{BASE}/contacts/{ID}")
def contacts_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "contact": sales.update_contact(
                connection, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/contacts/{ID}")
def contacts_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        sales.delete_contact(connection, request.match.group(1))
    request.audit("studio.contact_delete", "studio_contact", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/deals")
def deals_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "deals": sales.list_deals(
                connection,
                stage=request.q("stage"),
                client_id=request.q("client_id"),
                q=request.q("q"),
                owner=request.q("owner"),
                status=request.q("status"),
            ),
            "probabilities": sales.probabilities(connection),
        }


@aira_route("GET", f"{BASE}/deals/board")
def deals_board(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, sales.board(connection)


@aira_route("GET", f"{BASE}/deals/export")
def deals_export(request: Request) -> Raw:
    with request.connect() as connection:
        store.ensure(connection)
        rows = sales.list_deals(connection)
    columns = (
        "title",
        "client_name",
        "contact_name",
        "stage",
        "probability",
        "value_cents",
        "weighted_cents",
        "currency",
        "expected_close",
        "owner_name",
        "source",
        "next_step",
        "lost_reason",
        "created_at",
        "won_at",
    )
    return Raw(
        200,
        kit.csv_bytes(rows, columns),
        "text/csv; charset=utf-8",
        filename="deals.csv",
    )


@aira_route("POST", f"{BASE}/deals")
def deals_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        deal = sales.create_deal(connection, request.payload)
    request.audit("studio.deal_create", "studio_deal", deal["id"])
    return 201, {"deal": deal}


@aira_route("GET", f"{BASE}/deals/{ID}")
def deals_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        deal = sales.get_deal(connection, request.match.group(1))
        return 200, {
            "deal": deal,
            "activities": sales.list_activities(
                connection, today=_today(), deal_id=deal["id"]
            ),
            "contacts": sales.list_contacts(connection, deal["client_id"]),
        }


@aira_route("PATCH", f"{BASE}/deals/{ID}")
def deals_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        deal = sales.update_deal(connection, request.match.group(1), request.payload)
    request.audit("studio.deal_update", "studio_deal", deal["id"])
    return 200, {"deal": deal}


@aira_route("POST", f"{BASE}/deals/{ID}/move")
def deals_move(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        deal = sales.move_deal(
            connection,
            request.match.group(1),
            str(request.payload.get("stage") or ""),
            lost_reason=str(request.payload.get("lost_reason") or ""),
        )
    request.audit(
        "studio.deal_move", "studio_deal", deal["id"], {"stage": deal["stage"]}
    )
    return 200, {"deal": deal}


@aira_route("POST", f"{BASE}/deals/{ID}/convert")
def deals_convert(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        made = sales.convert_deal(connection, request.match.group(1), today=_today())
    request.audit("studio.deal_convert", "studio_deal", request.match.group(1))
    return 200, made


@aira_route("DELETE", f"{BASE}/deals/{ID}")
def deals_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        sales.delete_deal(connection, request.match.group(1))
    request.audit("studio.deal_delete", "studio_deal", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/activities")
def activities_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "activities": sales.list_activities(
                connection,
                today=_today(),
                when=request.q("when"),
                client_id=request.q("client_id"),
                deal_id=request.q("deal_id"),
            )
        }


@aira_route("POST", f"{BASE}/activities")
def activities_create(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        activity = sales.add_activity(connection, request.payload, today=_today())
    return 201, {"activity": activity}


@aira_route("PATCH", f"{BASE}/activities/{ID}")
def activities_update(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {
            "activity": sales.update_activity(
                connection, request.match.group(1), request.payload, today=_today()
            )
        }


@aira_route("DELETE", f"{BASE}/activities/{ID}")
def activities_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        sales.delete_activity(connection, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/forecast")
def forecast(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, sales.forecast(
            connection, today=_today(), months=request.qint("months", 6, 1, 12)
        )


@aira_route("GET", f"{BASE}/pipeline")
def pipeline_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {"probabilities": sales.probabilities(connection)}


@aira_route("POST", f"{BASE}/pipeline")
def pipeline_save(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        saved = sales.save_probabilities(connection, request.payload)
    request.audit("studio.pipeline_settings", "studio", "pipeline")
    return 200, {"probabilities": saved}


@aira_route("GET", f"{BASE}/leads")
def leads_importable(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        return 200, sales.importable_leads(connection, q=request.q("q"))


@aira_route("POST", f"{BASE}/leads/{ID}/import")
def leads_import(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        store.ensure(connection)
        deal = sales.import_lead(connection, request.match.group(1), request.payload)
    request.audit("studio.lead_import", "studio_deal", deal["id"])
    return 201, {"deal": deal}


# ---------------------------------------------------------------------------
# public pages
# ---------------------------------------------------------------------------


@public.page(APP, "proposal")
def proposal_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.public_proposal(connection, link["ref_id"], _today())


@public.action(APP, "proposal", "accept")
def proposal_accept(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    if not kit.flag(payload.get("agree")):
        raise ValueError("Tick the box to confirm you accept the proposal.")
    service.accept_proposal(
        connection,
        link["ref_id"],
        name=str(payload.get("name") or ""),
        visitor=visitor,
        today=_today(),
    )
    return 200, {
        "proposal": service.public_proposal(connection, link["ref_id"], _today())
    }


@public.action(APP, "proposal", "decline")
def proposal_decline(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    service.decline_proposal(
        connection,
        link["ref_id"],
        reason=str(payload.get("reason") or ""),
        today=_today(),
    )
    return 200, {
        "proposal": service.public_proposal(connection, link["ref_id"], _today())
    }


@public.page(APP, "invoice")
def invoice_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.public_invoice(connection, link["ref_id"], _today())


@public.action(APP, "invoice", "notify_paid")
def invoice_notify_paid(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """The client says they paid. The business checks and records the payment; nothing is marked paid here."""
    store.ensure(connection)
    current = service.public_invoice(connection, link["ref_id"], _today())
    if current["status"] in ("paid", "void"):
        raise ValueError("This invoice has nothing left to pay.")
    service.add_customer_notice(
        connection,
        link["ref_id"],
        reference=kit.text(
            payload.get("reference"), "a payment reference", max_len=120
        ),
        note=kit.text(payload.get("note"), "a note", max_len=500, multiline=True),
    )
    return 200, {
        "invoice": service.public_invoice(connection, link["ref_id"], _today()),
        "thanks": True,
    }


# ---------------------------------------------------------------------------
# the Business hub
# ---------------------------------------------------------------------------


@kit.checklist(APP)
def _checklist(connection: Any) -> list[dict[str, Any]]:
    store.ensure(connection)
    return service.checklist(connection)


@kit.summary(APP)
def _summary(connection: Any) -> dict[str, Any]:
    store.ensure(connection)
    return service.summary(connection, today=_today())

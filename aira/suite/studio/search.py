"""Search across Sales: the box across the top of the app finds clients (and the people at them), deals,
proposals, invoices and projects by name, number, client, email or phone. Each kind is capped, newest first, so
a business with years of invoices still answers as the person types."""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit
from aira.suite.studio import sales, service, store

KINDS = ("clients", "deals", "proposals", "invoices", "projects")


def _like(q: str) -> str:
    # % and _ are wildcards in LIKE: a search for "50%" means the text, not "50 and anything".
    text = q.strip()[:80].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{text}%"


def _rows(
    connection: sqlite3.Connection, sql: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    return [store.decode(row) for row in kit.fetch_all(connection, sql, params)]


def search(
    connection: sqlite3.Connection, q: str, *, today: str, limit: int = 5
) -> dict[str, Any]:
    """Up to ``limit`` of each kind whose words hold ``q``; empty lists for an empty query."""
    limit = max(1, min(int(limit or 5), 50))
    found: dict[str, Any] = {kind: [] for kind in KINDS}
    if not q.strip():
        return {"q": "", **found}
    like = _like(q)
    client_match = "SELECT id FROM st_clients WHERE name LIKE :q ESCAPE '\\' OR company LIKE :q ESCAPE '\\'"
    params = {"q": like, "limit": limit}

    clients = _rows(
        connection,
        """SELECT c.*, (SELECT k.name FROM st_contacts k WHERE k.client_id = c.id
                AND (k.name LIKE :q ESCAPE '\\' OR k.email LIKE :q ESCAPE '\\' OR k.phone LIKE :q ESCAPE '\\') LIMIT 1) AS contact_hit
            FROM st_clients c
            WHERE c.name LIKE :q ESCAPE '\\' OR c.company LIKE :q ESCAPE '\\' OR c.email LIKE :q ESCAPE '\\'
                OR c.phone LIKE :q ESCAPE '\\' OR c.tags LIKE :q ESCAPE '\\' OR c.id IN (SELECT client_id FROM st_contacts
                WHERE name LIKE :q ESCAPE '\\' OR email LIKE :q ESCAPE '\\' OR phone LIKE :q ESCAPE '\\')
            ORDER BY CASE WHEN c.name LIKE :start ESCAPE '\\' THEN 0 ELSE 1 END, c.updated_at DESC LIMIT :limit""",
        {**params, "start": like[1:]},
    )
    found["clients"] = [
        {
            "id": c["id"],
            "name": c["name"],
            "company": c["company"],
            "email": c["email"],
            "status": c["status"],
            "contact": c.get("contact_hit") or "",
        }
        for c in clients
    ]

    probs = sales.probabilities(connection)
    found["deals"] = [
        {
            key: deal[key]
            for key in (
                "id",
                "title",
                "stage",
                "value_cents",
                "expected_close",
                "client_name",
                "client_company",
                "open",
            )
        }
        for deal in (
            sales._deal_view(connection, row, probs)
            for row in _rows(
                connection,
                f"""SELECT * FROM st_deals WHERE title LIKE :q ESCAPE '\\' OR next_step LIKE :q ESCAPE '\\'
                    OR client_id IN ({client_match}) ORDER BY updated_at DESC LIMIT :limit""",  # noqa: S608
                params,
            )
        )
    ]

    found["proposals"] = [
        {
            key: proposal[key]
            for key in (
                "id",
                "number",
                "title",
                "status",
                "total_cents",
                "valid_until",
                "client_name",
                "client_company",
            )
        }
        for proposal in (
            {
                **service.proposal_view(row, today),
                **service._client_name(connection, row["client_id"]),
            }
            for row in _rows(
                connection,
                f"""SELECT * FROM st_proposals WHERE number LIKE :q ESCAPE '\\' OR title LIKE :q ESCAPE '\\'
                    OR client_id IN ({client_match}) ORDER BY seq DESC LIMIT :limit""",  # noqa: S608
                params,
            )
        )
    ]

    found["invoices"] = [
        {
            key: invoice[key]
            for key in (
                "id",
                "number",
                "status",
                "total_cents",
                "balance_cents",
                "due_date",
                "client_name",
                "client_company",
            )
        }
        for invoice in (
            service.invoice_view(connection, row, today)
            for row in _rows(
                connection,
                f"""SELECT * FROM st_invoices WHERE number LIKE :q ESCAPE '\\'
                    OR client_id IN ({client_match}) ORDER BY seq DESC LIMIT :limit""",  # noqa: S608
                params,
            )
        )
    ]

    found["projects"] = [
        {
            "id": p["id"],
            "name": p["name"],
            "status": p["status"],
            "deadline": p["deadline"],
            **service._client_name(connection, p["client_id"]),
        }
        for p in _rows(
            connection,
            f"""SELECT * FROM st_projects WHERE name LIKE :q ESCAPE '\\'
                OR client_id IN ({client_match}) ORDER BY updated_at DESC LIMIT :limit""",  # noqa: S608
            params,
        )
    ]
    for project in found["projects"]:
        project.pop("client_email", None)
    return {"q": q.strip()[:80], **found}

"""The hosted build's erasure: a member removed by an administrator, a member deleting their own account, and a
data subject request erase the person with :mod:`aira.erasure`.

* In the organization's database (``_org/api.sqlite3``, where the life apps keep every member's Health, Planner,
  AI chat, Learn, Play, News, notifications and push subscriptions, keyed by their profile) and in the shared
  Social and Videos databases (one for the platform).
* The platform's own records that carry the member's email: ``mail_outbox`` (mail delivered or captured: one on its
  way keeps its address, and the link to their data export, sent as they leave, still reaches them) and
  the payloads of billing webhooks and outgoing webhook deliveries. Those are redacted, never deleted: the books
  and delivery logs keep their rows.
* A member's careers workspace and own business space follow the organization's data policy as before
  (:mod:`monosage_cloud.tenancy`): they are deleted or handed to the person, or kept by the organization.

Removal by an administrator erases the person's own life, not their place in the organization's business records
(they may still be its customer); deleting their own account, or a data subject request, erases those too. Under the
organization's legal hold only Social and Videos (outside the organization) are erased; the rest waits.

Each entry point never raises: the removal or deletion it belongs to goes on, and a failure is logged. The
receipt goes to the organization's audit log (``member.erasure``) with counts only, never the person.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from aira import erasure
from monosage_cloud.db import many, one

logger = logging.getLogger("monosage_cloud.erasure")

# what removal by an administrator leaves alone: the organization's business records of the person as a customer
BUSINESS_APPS = (
    "shop",
    "bookings",
    "marketing",
    "service",
    "events",
    "academy",
    "studio",
    "payments",
    "leads",
    "sweep",
)
OUTSIDE_THE_ORGANIZATION = ("social", "videos")
REDACTED = "[erased]"
# mail that is redacted only once it was delivered (or kept in the clear): a message on its way keeps its address
DELIVERED = ("sent", "failed", "captured")
# the link to their own data, sent as they leave: it must reach them, and it names nothing else about them
HANDOVER_KINDS = ("data_export_ready",)


def member_removed(
    app: Any, org_id: str, target: dict[str, Any], *, actor: Any = None
) -> dict[str, Any]:
    """An administrator removed this member (``target``: their ``members`` row)."""
    return _erase(
        app,
        org_id,
        str(target.get("member_id") or ""),
        email=str(target.get("email") or ""),
        name=str(target.get("name") or ""),
        reason="member_removed",
        actor=actor,
        skip=BUSINESS_APPS,
    )


def member_deleted(
    app: Any, org_id: str, member_id: str, *, actor_label: str = ""
) -> dict[str, Any]:
    """The member deleted their own account, or a data subject request deletes them (before their row is
    anonymised: the email is read from it)."""
    try:
        with app.db.read() as connection:
            row = one(
                connection,
                "SELECT email, name FROM members WHERE org_id = ? AND member_id = ?",
                (org_id, member_id),
            )
    except Exception:  # noqa: BLE001 - the deletion it belongs to goes on (Social and Videos are still erased)
        logger.exception("Could not read the member being deleted in %s", org_id)
        row = None
    return _erase(
        app,
        org_id,
        member_id,
        email=str((row or {}).get("email") or ""),
        name=str((row or {}).get("name") or ""),
        reason="member_deleted",
        actor_label=actor_label or "self",
    )


def _erase(
    app: Any,
    org_id: str,
    member_id: str,
    *,
    email: str,
    name: str,
    reason: str,
    actor: Any = None,
    actor_label: str = "",
    skip: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not member_id:
        return {}
    try:
        held = bool(app.lifecycle.on_legal_hold(org_id))
        receipt = _erase_in_organization(
            app, org_id, member_id, email, name, reason, held=held, skip=skip
        )
        redacted = {} if held else redact_platform_records(app, org_id, email)
        detail = {
            **{
                k: receipt.get(k)
                for k in ("job_id", "reason", "status", "erased", "held", "failed")
            },
            "redacted": redacted,
            **({"organization_legal_hold": True} if held else {}),
        }
        app.audit.record(
            org_id,
            "member.erasure",
            actor=actor,
            actor_type="" if actor is not None else "system",
            actor_label="" if actor is not None else actor_label,
            target_type="erasure",
            target_id=str(receipt.get("job_id") or ""),
            detail=detail,
            source="erasure",
        )
        return detail
    except Exception:  # noqa: BLE001 - the removal or deletion it belongs to goes on
        logger.exception("Erasing a member of %s failed", org_id)
        return {"status": "failed"}


def _erase_in_organization(
    app: Any,
    org_id: str,
    member_id: str,
    email: str,
    name: str,
    reason: str,
    *,
    held: bool,
    skip: tuple[str, ...],
) -> dict[str, Any]:
    """Run the erasure in the organization's database (the member's life apps), or, under a legal hold or for an
    organization that never opened its apps, only in Social and Videos."""
    path = app.workspaces.org_api_path(org_id)
    if held or not path.exists():
        connection = sqlite3.connect(":memory:")
        apps: list[str] | None = list(OUTSIDE_THE_ORGANIZATION)
    else:
        connection = app.workspaces.org_api_connection(org_id)
        apps = [a for a in erasure.registered() if a not in skip] if skip else None
    try:
        connection.row_factory = sqlite3.Row
        profile = None
        if erasure.table_exists(connection, "life_profiles"):
            profile = connection.execute(
                "SELECT id, email, name FROM life_profiles WHERE member_ref = ?",
                (member_id,),
            ).fetchone()
        person = erasure.Person(
            profile_id=profile["id"] if profile else "",
            member_id=member_id,
            emails=(email, (profile["email"] if profile else "") or ""),
            names=(name, (profile["name"] if profile else "") or ""),
        )
        return erasure.run_erasure(
            connection, person, reason=reason, actor="organization", apps=apps
        )
    finally:
        connection.close()


def redact_platform_records(app: Any, org_id: str, email: str) -> dict[str, int]:
    """Take the member's email out of the platform's mail outbox and webhook payloads (rows stay)."""
    email = str(email or "").strip().lower()
    if "@" not in email:
        return {}
    pattern = f"%{email}%"
    counts: dict[str, int] = {}
    with app.db.write() as connection:
        for mail in many(
            connection,
            f"""SELECT mail_id, recipient, subject, body FROM mail_outbox WHERE org_id IN (?, '')
            AND status IN ({", ".join("?" for _ in DELIVERED)}) AND kind NOT IN ({", ".join("?" for _ in HANDOVER_KINDS)})
            AND (LOWER(recipient) = ? OR LOWER(subject) LIKE ? OR LOWER(body) LIKE ?)""",  # noqa: S608 - placeholders only
            (org_id, *DELIVERED, *HANDOVER_KINDS, email, pattern, pattern),
        ):
            to_them = str(mail["recipient"] or "").strip().lower() == email
            connection.execute(
                "UPDATE mail_outbox SET recipient = ?, subject = ?, body = ? WHERE mail_id = ?",
                (
                    REDACTED if to_them else mail["recipient"],
                    REDACTED if to_them else _redact(mail["subject"], email),
                    "" if to_them else _redact(mail["body"], email),
                    mail["mail_id"],
                ),
            )
            counts["mail_outbox"] = counts.get("mail_outbox", 0) + 1
        for table, key in (
            ("billing_events", "event_id"),
            ("webhook_deliveries", "delivery_id"),
        ):
            for found in many(
                connection,
                f"SELECT {key}, payload FROM {table} WHERE org_id IN (?, '') AND LOWER(payload) LIKE ?",  # noqa: S608 - fixed names
                (org_id, pattern),
            ):
                connection.execute(
                    f"UPDATE {table} SET payload = ? WHERE {key} = ?",  # noqa: S608 - fixed names
                    (_redact(found["payload"], email), found[key]),
                )
                counts[table] = counts.get(table, 0) + 1
    return counts


def _redact(text: Any, email: str) -> str:
    import re

    return re.sub(re.escape(email), REDACTED, str(text or ""), flags=re.IGNORECASE)

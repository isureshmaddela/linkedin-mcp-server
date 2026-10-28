"""Transactional email: an outbox in the database, delivered over SMTP when configured.

Without ``AIRA_CLOUD_SMTP_URL`` every message is stored with status ``captured``
(development and tests read it back from the outbox). Teams and Enterprise get
their organization's name and logo in the message; no tenant domain is used.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any
from urllib.parse import unquote, urlsplit

from aira_cloud.config import CloudConfig, Clock
from aira_cloud.db import ControlDB, loads, many, new_id, one

logger = logging.getLogger("aira_cloud.mail")

TEMPLATES: dict[str, tuple[str, str]] = {
    "verify_email": (
        "Confirm your email for {org}",
        "Hello {name},\n\nConfirm your email address to finish setting up {org}:\n{link}\n\nThe link works once and expires in 24 hours.",
    ),
    "invite": (
        "{inviter} invited you to {org} on Monosage",
        "Hello,\n\n{inviter} invited you to join {org} on Monosage Career Copilot as {role}.\nAccept the invitation: {link}\n\nThe link works once and expires on {expires}.",
    ),
    "password_reset": (
        "Reset your Monosage password",
        "Hello {name},\n\nSomeone asked to reset the password for your {org} account. If it was you, choose a new password here:\n{link}\n\nThe link works once and expires in 24 hours. If it was not you, ignore this message.",
    ),
    "payment_failed": (
        "Payment failed for {org}",
        "The latest payment for {org} did not go through. Automation has stopped. The organization becomes read-only on {read_only_on} unless payment is updated:\n{link}",
    ),
    "read_only": (
        "{org} is now read-only",
        "Payment for {org} is {days} days overdue, so data is read-only. Update the payment method to restore access:\n{link}\nThe organization is scheduled for deletion on {deletion_on} unless payment is restored.",
    ),
    "deletion_notice": (
        "Final notice: {org} will be deleted",
        "{org} is scheduled for deletion on {deletion_on}. Export your data and update billing before then:\n{link}",
    ),
    "trial_ending": (
        "Your {org} trial ends on {trial_end}",
        "The trial for {org} ends on {trial_end}. Pick a plan to keep your candidates' searches and applications running:\n{link}",
    ),
    "subscription_cancelled": (
        "Subscription cancelled for {org}",
        "The subscription for {org} is cancelled. Data stays readable and exportable until {export_until}:\n{link}",
    ),
    "support_request": (
        "Monosage support asks for access to {org}",
        "{operator} from Monosage support asked for time-limited access to {org}.\nReason: {reason}\nReview and approve or deny the request:\n{link}",
    ),
    "downgrade_pause": (
        "{org} is over its new plan limits",
        "After the plan change, {org} is over these limits: {limits}. Automation is paused until the organization is within them:\n{link}",
    ),
    "data_export_ready": (
        "Your {org} data export is ready",
        "Your export is ready to download:\n{link}",
    ),
}


class Mailer:
    def __init__(self, db: ControlDB, clock: Clock, config: CloudConfig) -> None:
        self.db = db
        self.clock = clock
        self.config = config
        self.branding: Callable[[str], dict[str, Any]] = lambda org_id: {}

    def send(self, org_id: str, to: str, kind: str, **context: Any) -> str:
        subject_template, body_template = TEMPLATES[kind]
        branding = self.branding(org_id) if org_id else {}
        org_name = str(context.get("org") or branding.get("name") or "Monosage")
        values = {"name": "there", "org": org_name, **context}
        subject = subject_template.format(**values)
        body = body_template.format(**values)
        if branding.get("logo_url"):
            body = f"[{org_name}] {branding['logo_url']}\n\n{body}"
        elif branding.get("branded"):
            body = f"[{org_name}]\n\n{body}"
        body += "\n\n—\nAira Career Copilot"
        mail_id = new_id()
        status, error = "captured", ""
        if self.config.smtp_url:
            try:
                self._deliver(to, subject, body)
                status = "sent"
            except Exception as exc:  # noqa: BLE001 - a mail failure must not fail the action
                status, error = "failed", f"{type(exc).__name__}: {exc}"[:300]
                logger.warning("Mail delivery failed: %s", error)
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO mail_outbox (mail_id, org_id, recipient, subject, body, kind, status, error, created_at, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mail_id,
                    org_id,
                    to,
                    subject,
                    body,
                    kind,
                    status,
                    error,
                    self.clock.now(),
                    self.clock.now() if status == "sent" else None,
                ),
            )
        return mail_id

    def _deliver(self, to: str, subject: str, body: str) -> None:
        parts = urlsplit(self.config.smtp_url)
        message = EmailMessage()
        message["From"] = self.config.mail_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        host, port = (
            parts.hostname or "localhost",
            parts.port or (465 if parts.scheme == "smtps" else 587),
        )
        if parts.scheme == "smtps":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                host, port, context=ssl.create_default_context(), timeout=15
            )
        else:
            smtp = smtplib.SMTP(host, port, timeout=15)
        with smtp:
            if parts.scheme == "smtp" and port != 25:
                smtp.starttls(context=ssl.create_default_context())
            if parts.username:
                smtp.login(unquote(parts.username), unquote(parts.password or ""))
            smtp.send_message(message)

    def outbox(
        self, *, org_id: str = "", recipient: str = "", kind: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        clauses, params = ["1 = 1"], []
        for column, value in (
            ("org_id", org_id),
            ("recipient", recipient),
            ("kind", kind),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        with self.db.read() as connection:
            return many(
                connection,
                f"SELECT * FROM mail_outbox WHERE {' AND '.join(clauses)} ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*params, limit),
            )

    def latest(self, recipient: str, kind: str) -> dict[str, Any] | None:
        rows = self.outbox(recipient=recipient, kind=kind, limit=1)
        return rows[0] if rows else None

    def delete_org(self, org_id: str) -> None:
        with self.db.write() as connection:
            connection.execute("DELETE FROM mail_outbox WHERE org_id = ?", (org_id,))


def link_from(mail: dict[str, Any] | None) -> str:
    """The first URL in a captured message (tests follow it like a person would)."""
    if not mail:
        return ""
    for token in mail["body"].split():
        if token.startswith(("http://", "https://")):
            return token.rstrip(".,")
    return ""


__all__ = ["Mailer", "TEMPLATES", "link_from", "loads", "one"]

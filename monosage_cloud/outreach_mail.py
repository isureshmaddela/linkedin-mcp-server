"""The organization's mail for a member's own outreach email (recruiter email from a list).

A hosted member has no mail server of their own: Business hub > Email belongs to the organization (owners,
admins and coaches set it up in its shared workspace). Recruiter email goes out through that server:

* the From address and server are the organization's, the display name is the member's ("Jordan Lee via Acme
  Careers", set by :mod:`aira.recruiter_outreach`) and Reply-To is the member's own sign-in address, so the
  answers reach them and an "unsubscribe" reply comes back to the person who must honour it;
* an address on the organization's do-not-contact list (Leads > Do not contact) is skipped, as is one on the
  member's own list;
* the plan's daily cap (``lead_email_daily_cap``) bounds the member's daily limit, and the plan, the operator's
  outbound switch and the organization's state can switch sending off (``unavailable``), which the page shows.

Who may send, the monthly allowance and the audit are the gateway's (``route_hooks.recruiter_email_send_pre``).
The messages, the daily limit and the send log stay in the member's workspace; the organization's database only
lends its mail settings, its password (decrypted under the organization's key) and its do-not-contact list.

:func:`business_mail` lends the same server to a member's own business apps (their space, monosage_cloud/spaces.py)
for customer email (a booking confirmation, an invoice, a course reminder) while the member has not set up mail of
their own in Business hub > Email: the member's name ("Jordan Lee via Acme Careers") and Reply-To their sign-in
address. Their own settings win whenever they are complete (aira.suite.kit.outgoing_mail).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aira.hosting import OutreachMail
from monosage_cloud.errors import NotFound
from monosage_cloud.kms import KeyProviderError

if TYPE_CHECKING:
    from monosage_cloud.app import CloudApp

FEATURE = "leads_outreach"  # outbound email (Team and up): the same feature Leads outreach sends with
NOT_IN_PLAN = "Recruiter email is not part of your organization's plan."
ON_ORGANIZATION_LIST = "On your organization's do-not-contact list."


def _organization_settings(app: CloudApp, org_id: str) -> tuple[dict[str, Any], str]:
    """The organization's Business hub > Email and its password."""
    from aira.airaapi import store as airaapi_store
    from aira.suite import kit

    if not app.workspaces.org_api_path(org_id).exists():
        return dict(kit.EMAIL_SETTINGS_DEFAULTS), ""
    connection = app.workspaces.org_api_connection(org_id)
    try:
        settings = kit.email_settings(connection)
        try:
            password = (
                airaapi_store.SecretStore(app.secrets.key(org_id)).get(
                    connection, kit.SMTP_PASSWORD_SECRET
                )
                or ""
            )
        except KeyProviderError:
            raise  # an unreachable KMS is an outage, not "no password"
        except Exception:  # noqa: BLE001 - an unreadable password means "none saved"
            password = ""
    finally:
        connection.close()
    return settings, password


def business_mail(app: CloudApp, org_id: str, member_id: str) -> OutreachMail | None:
    """The organization's mail server for a member's own business apps, or None when it has none set up."""
    settings, password = _organization_settings(app, org_id)
    if not (settings.get("smtp_host") and settings.get("from_email")):
        return None
    try:
        member = app.tenancy.member(org_id, member_id)
    except NotFound:  # no longer a member: nothing is sent in their name
        return None
    writer = (
        " ".join(str(member.get("name") or "").split())
        or str(member["email"]).split("@")[0]
    )
    organization = " ".join(
        str(settings.get("from_name") or app.tenancy.org(org_id)["name"]).split()
    )
    lent = {
        **settings,
        "from_name": (f"{writer} via {organization}" if organization else writer)[:100],
        "reply_to": str(member["email"]),
        "auto_send": [],  # what is sent without approval is the member's choice, in their own Business hub
    }
    return OutreachMail(
        settings=lent, password=password, suppressed=lambda _address: ""
    )


def organization_mail(app: CloudApp, org_id: str, candidate_id: str) -> OutreachMail:
    from aira.airaapi import store as airaapi_store
    from aira.leads import mail as leads_mail
    from aira.suite import kit

    settings = dict(kit.EMAIL_SETTINGS_DEFAULTS)
    password = ""
    suppressions: set[str] = set()
    if app.workspaces.org_api_path(org_id).exists():
        connection = app.workspaces.org_api_connection(org_id)
        try:
            settings = kit.email_settings(connection)
            try:
                password = (
                    airaapi_store.SecretStore(app.secrets.key(org_id)).get(
                        connection, kit.SMTP_PASSWORD_SECRET
                    )
                    or ""
                )
            except KeyProviderError:
                raise  # an unreachable KMS is an outage, not "no password"
            except Exception:  # noqa: BLE001 - an unreadable password means "none saved"
                password = ""
            suppressions = {
                str(row[0])
                for row in connection.execute("SELECT key FROM lead_suppressions")
            }
        finally:
            connection.close()
    settings["reply_to"] = str(app.tenancy.member(org_id, candidate_id)["email"])

    def suppressed(address: str) -> str:
        email = str(address or "").strip().lower()
        if not email:
            return ""
        keys = {f"email:{email}"}
        domain = email.split("@")[-1]
        if domain not in leads_mail.FREEMAIL:
            keys.add(f"domain:{domain}")
        return ON_ORGANIZATION_LIST if keys & suppressions else ""

    entitlements = app.entitlements.resolve(org_id)
    unavailable = ""
    if not entitlements.has(FEATURE):
        unavailable = NOT_IN_PLAN
    else:
        block = app.gate.outreach_block(org_id, candidate_id)
        if block is not None:
            unavailable = block[2]
    return OutreachMail(
        settings=settings,
        password=password,
        suppressed=suppressed,
        daily_cap=int(entitlements.limit("lead_email_daily_cap") or 0),
        unavailable=unavailable,
    )


__all__ = [
    "organization_mail",
    "business_mail",
    "FEATURE",
    "NOT_IN_PLAN",
    "ON_ORGANIZATION_LIST",
]

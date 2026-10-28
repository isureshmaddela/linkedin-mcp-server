"""Single sign-on (OIDC, SAML 2.0) and SCIM 2.0 provisioning for Team and Enterprise organizations.

An organization has at most one identity provider (``sso_configs``). A successful sign-in resolves to a
member of that organization (linked by subject, then by email, else provisioned just in time for an
allowed email domain) and opens a session with ``auth_method == "sso"``, which satisfies MFA: the identity
provider does the second factor. Owners are never created or changed by group mapping, and keep password
plus MFA as the break-glass sign-in when SSO is enforced.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import zlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

from aira_cloud import crypto
from aira_cloud.db import INTEGRITY_ERRORS, dumps, loads, many, new_id, one
from aira_cloud.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PlanRequired,
    Unauthorized,
    Unavailable,
    ValidationFailed,
)
from aira_cloud.rbac import (
    ADMIN,
    AUDITOR,
    BILLING,
    CANDIDATE,
    COACH,
    ORG_ROLES,
    OWNER,
    Principal,
)

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.sso")

STATE_TTL = 600
SAML_SKEW = 120
JWKS_TTL = 300
MAX_SAML_BYTES = 2_000_000
DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)
# When several groups map to different roles, the most privileged wins. Owners are never granted by a group.
ROLE_PRIORITY = (ADMIN, COACH, AUDITOR, BILLING, CANDIDATE)
SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"
EMAIL_ATTRIBUTES = (
    "email",
    "mail",
    "emailaddress",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
)
NAME_ATTRIBUTES = (
    "displayname",
    "name",
    "cn",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "urn:oid:2.16.840.1.113730.3.1.241",
)
GROUP_ATTRIBUTES = (
    "groups",
    "memberof",
    "http://schemas.xmlsoap.org/claims/Group",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
)

SCIM_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCIM_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def _epoch(value: str) -> int:
    text = value.strip().replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _clean_domains(domains: Any) -> list[str]:
    if isinstance(domains, str):
        domains = [d for d in re.split(r"[,\s]+", domains) if d]
    if not isinstance(domains, (list, tuple)):
        raise ValidationFailed("Domains must be a list of email domains.")
    cleaned = []
    for domain in domains:
        value = str(domain).strip().lower().lstrip("@")
        if not DOMAIN_RE.fullmatch(value):
            raise ValidationFailed(f"'{domain}' is not a valid email domain.")
        if value not in cleaned:
            cleaned.append(value)
    if len(cleaned) > 20:
        raise ValidationFailed("At most 20 domains.")
    return cleaned


def _clean_group_map(mapping: Any) -> dict[str, str]:
    if mapping in (None, ""):
        return {}
    if not isinstance(mapping, dict):
        raise ValidationFailed("The group-to-role map must be an object.")
    cleaned = {}
    for group, role in mapping.items():
        if role not in ORG_ROLES or role == OWNER:
            raise ValidationFailed(
                "Groups can map to admin, coach, candidate, billing or auditor, never owner."
            )
        if not str(group).strip() or len(str(group)) > 200:
            raise ValidationFailed("Enter a group name of up to 200 characters.")
        cleaned[str(group).strip()] = role
    if len(cleaned) > 100:
        raise ValidationFailed("At most 100 group mappings.")
    return cleaned


def _clean_default_role(role: str) -> str:
    if role not in ORG_ROLES or role == OWNER:
        raise ValidationFailed(
            "The default role for new people can be admin, coach, candidate, billing or auditor."
        )
    return role


def _check_public_url(url: str, *, what: str) -> None:
    """Organization-supplied URLs (identity provider, model endpoint) must be https to a public host.

    Loopback http is a development opt-in (``AIRA_CLOUD_ALLOW_LOCAL_URLS=1``). The host is resolved when the URL
    is saved; a deployment that must defend against DNS rebinding should also block internal ranges at the network.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    allow_local = (
        os.getenv("AIRA_CLOUD_ALLOW_LOCAL_IDP", "") == "1"
        or os.getenv("AIRA_CLOUD_ALLOW_LOCAL_URLS", "") == "1"
    )
    if parts.scheme not in {"https", "http"} or not host:
        raise ValidationFailed(f"The {what} must be an https:// address.")
    try:
        addresses = {
            ipaddress.ip_address(info[4][0])
            for info in socket.getaddrinfo(
                host, parts.port or (443 if parts.scheme == "https" else 80)
            )
        }
    except (socket.gaierror, ValueError):
        raise ValidationFailed(f"The {what} host could not be resolved.") from None
    private = [
        a
        for a in addresses
        if a.is_private
        or a.is_loopback
        or a.is_link_local
        or a.is_reserved
        or a.is_multicast
    ]
    if private and not allow_local:
        raise ValidationFailed(f"The {what} must point to a public host.")
    if parts.scheme == "http" and not (
        allow_local and all(a.is_loopback for a in addresses)
    ):
        raise ValidationFailed(f"The {what} must use https.")


def _fetch(
    url: str, *, data: dict[str, str] | None = None, timeout: int = 10
) -> dict[str, Any]:
    body = urlencode(data).encode() if data is not None else None
    request = Request(
        url,
        data=body,
        method="POST" if data is not None else "GET",
        headers={
            "Accept": "application/json",
            **(
                {"Content-Type": "application/x-www-form-urlencoded"}
                if data is not None
                else {}
            ),
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - the URL passed _check_public_url
            return json.loads(response.read(2_000_000).decode("utf-8"))
    except HTTPError as error:
        raise Unavailable(
            f"The identity provider answered {error.code}.", code="idp_error"
        ) from None
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise Unavailable(
            f"The identity provider could not be reached: {type(error).__name__}.",
            code="idp_unreachable",
        ) from None


class Sso:
    def __init__(self, app: CloudApp) -> None:
        self.app = app
        self._jwks: dict[str, tuple[int, list[dict[str, Any]]]] = {}

    @property
    def db(self):  # noqa: ANN201
        return self.app.db

    @property
    def now(self) -> int:
        return self.app.clock.now()

    # ------------------------------------------------------------------ configuration

    def get_config(self, org_id: str) -> dict[str, Any] | None:
        with self.db.read() as connection:
            row = one(
                connection, "SELECT * FROM sso_configs WHERE org_id = ?", (org_id,)
            )
        if row is None:
            return None
        row["config"] = loads(row["config"], {})
        row["domains"] = loads(row["domains"], [])
        row["group_role_map"] = loads(row["group_role_map"], {})
        return row

    def enforced_for(self, org_id: str) -> bool:
        config = self.get_config(org_id)
        if (
            not config
            or not config["enforced"]
            or config["protocol"] not in {"oidc", "saml"}
        ):
            return False
        try:
            return self.app.entitlements.resolve(org_id).has(
                "auth.sso"
            )  # a downgrade must not lock people out
        except NotFound:
            return False

    def sp_details(self, org_id: str) -> dict[str, str]:
        slug = self.app.tenancy.org(org_id)["slug"]
        base = self.app.config.base_url.rstrip("/")
        return {
            "slug": slug,
            "login_url": f"{base}/api/sso/{slug}/start",
            "oidc_redirect_uri": f"{base}/api/sso/oidc/callback",
            "saml_entity_id": f"{base}/api/sso/{slug}/saml/metadata",
            "saml_acs_url": f"{base}/api/sso/{slug}/saml/acs",
            "saml_metadata_url": f"{base}/api/sso/{slug}/saml/metadata",
            "scim_base_url": f"{base}/scim/v2",
        }

    def public_config(self, org_id: str) -> dict[str, Any]:
        """The configuration as an administrator sees it: never a secret."""
        config = self.get_config(org_id)
        details = self.sp_details(org_id)
        if not config or config["protocol"] not in {"oidc", "saml"}:
            return {"configured": False, "sp": details}
        settings = {k: v for k, v in config["config"].items() if k != "scim_groups"}
        if config["protocol"] == "oidc":
            settings["client_secret_set"] = self.app.secrets.exists(
                org_id, "sso_oidc_secret"
            )
        return {
            "configured": True,
            "protocol": config["protocol"],
            "enforced": bool(config["enforced"]),
            "domains": config["domains"],
            "default_role": config["default_role"],
            "group_role_map": config["group_role_map"],
            "settings": settings,
            "updated_at": config["updated_at"],
            "sp": details,
        }

    def _require_manage(self, actor: Principal) -> None:
        self.app.authz.require(actor, "sso.manage")
        self.app.entitlements.require_state(actor.org_id, write=True)
        self.app.entitlements.require_feature(
            actor.org_id, "auth.sso", label="Single sign-on"
        )

    def _save(
        self,
        org_id: str,
        protocol: str,
        settings: dict[str, Any],
        *,
        domains: list[str],
        group_map: dict[str, str],
        default_role: str,
        enforced: bool,
    ) -> None:
        existing = self.get_config(org_id)
        if existing and existing["config"].get("scim_groups"):
            settings["scim_groups"] = existing["config"][
                "scim_groups"
            ]  # provisioning state survives reconfiguration
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO sso_configs (org_id, protocol, config, enforced, domains, group_role_map, default_role, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(org_id) DO UPDATE SET protocol = excluded.protocol, config = excluded.config,
                enforced = excluded.enforced, domains = excluded.domains, group_role_map = excluded.group_role_map,
                default_role = excluded.default_role, updated_at = excluded.updated_at""",
                (
                    org_id,
                    protocol,
                    dumps(settings),
                    int(enforced),
                    dumps(domains),
                    dumps(group_map),
                    default_role,
                    self.now,
                ),
            )

    def configure_oidc(
        self,
        actor: Principal,
        *,
        issuer: str,
        client_id: str,
        client_secret: str = "",
        domains: Any = (),
        default_role: str = CANDIDATE,
        group_role_map: Any = None,
        groups_claim: str = "groups",
        enforced: bool = False,
    ) -> dict[str, Any]:
        self._require_manage(actor)
        issuer = str(issuer or "").strip().rstrip("/")
        client_id = str(client_id or "").strip()
        if not issuer or not client_id:
            raise ValidationFailed("Enter the issuer URL and the client id.")
        _check_public_url(issuer, what="issuer URL")
        discovery = _fetch(f"{issuer}/.well-known/openid-configuration")
        if str(discovery.get("issuer", "")).rstrip("/") != issuer:
            raise ValidationFailed(
                "The provider's discovery document names a different issuer."
            )
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not discovery.get(key):
                raise ValidationFailed(f"The discovery document has no {key}.")
            _check_public_url(str(discovery[key]), what=key.replace("_", " "))
        previous = self.get_config(actor.org_id)
        if not client_secret and not (
            previous
            and previous["protocol"] == "oidc"
            and self.app.secrets.exists(actor.org_id, "sso_oidc_secret")
        ):
            raise ValidationFailed("Enter the client secret.")
        if client_secret:
            self.app.secrets.put(actor.org_id, "sso_oidc_secret", client_secret)
        settings = {
            "issuer": issuer,
            "client_id": client_id,
            "authorization_endpoint": discovery["authorization_endpoint"],
            "token_endpoint": discovery["token_endpoint"],
            "jwks_uri": discovery["jwks_uri"],
            "groups_claim": str(groups_claim or "groups")[:80],
        }
        domain_list, group_map = (
            _clean_domains(domains),
            _clean_group_map(group_role_map),
        )
        self._save(
            actor.org_id,
            "oidc",
            settings,
            domains=domain_list,
            group_map=group_map,
            default_role=_clean_default_role(default_role),
            enforced=bool(enforced),
        )
        self.app.audit.record(
            actor.org_id,
            "sso.configure",
            actor=actor,
            detail={
                "protocol": "oidc",
                "issuer": issuer,
                "enforced": bool(enforced),
                "domains": domain_list,
            },
        )
        return self.public_config(actor.org_id)

    def configure_saml(
        self,
        actor: Principal,
        *,
        idp_entity_id: str,
        sso_url: str,
        idp_certificate_pem: str,
        domains: Any = (),
        default_role: str = CANDIDATE,
        group_role_map: Any = None,
        attribute_map: Any = None,
        enforced: bool = False,
    ) -> dict[str, Any]:
        self._require_manage(actor)
        idp_entity_id, sso_url = (
            str(idp_entity_id or "").strip(),
            str(sso_url or "").strip(),
        )
        if not idp_entity_id or not sso_url:
            raise ValidationFailed(
                "Enter the identity provider's entity id and single sign-on URL."
            )
        _check_public_url(sso_url, what="single sign-on URL")
        certificates = self._parse_certificates(idp_certificate_pem)
        attributes = {}
        if attribute_map:
            if not isinstance(attribute_map, dict) or not all(
                k in {"email", "name", "groups"} and isinstance(v, str)
                for k, v in attribute_map.items()
            ):
                raise ValidationFailed(
                    "The attribute map may set email, name and groups to attribute names."
                )
            attributes = {
                k: v.strip()[:200] for k, v in attribute_map.items() if v.strip()
            }
        settings = {
            "idp_entity_id": idp_entity_id,
            "sso_url": sso_url,
            "idp_certificate": "\n".join(certificates),
            "attribute_map": attributes,
        }
        domain_list, group_map = (
            _clean_domains(domains),
            _clean_group_map(group_role_map),
        )
        self._save(
            actor.org_id,
            "saml",
            settings,
            domains=domain_list,
            group_map=group_map,
            default_role=_clean_default_role(default_role),
            enforced=bool(enforced),
        )
        self.app.audit.record(
            actor.org_id,
            "sso.configure",
            actor=actor,
            detail={
                "protocol": "saml",
                "idp": idp_entity_id,
                "enforced": bool(enforced),
                "domains": domain_list,
            },
        )
        return self.public_config(actor.org_id)

    @staticmethod
    def _parse_certificates(pem: str) -> list[str]:
        from cryptography import x509

        blocks = re.findall(
            r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----",
            str(pem or ""),
            flags=re.S,
        )
        if not blocks:
            raise ValidationFailed(
                "Paste the identity provider's signing certificate in PEM format."
            )
        out = []
        for block in blocks[:4]:
            try:
                x509.load_pem_x509_certificate(block.encode())
            except ValueError:
                raise ValidationFailed(
                    "The signing certificate could not be read."
                ) from None
            out.append(block)
        return out

    def remove_config(self, actor: Principal) -> None:
        self.app.authz.require(actor, "sso.manage")
        self.app.entitlements.require_state(actor.org_id, write=True)
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT config FROM sso_configs WHERE org_id = ?",
                (actor.org_id,),
            )
            if row is None:
                raise NotFound("Single sign-on is not configured.")
            scim_groups = loads(row["config"], {}).get("scim_groups")
            if scim_groups:  # keep provisioning state; only the identity provider goes
                connection.execute(
                    "UPDATE sso_configs SET protocol = 'none', enforced = 0, config = ?, updated_at = ? WHERE org_id = ?",
                    (dumps({"scim_groups": scim_groups}), self.now, actor.org_id),
                )
            else:
                connection.execute(
                    "DELETE FROM sso_configs WHERE org_id = ?", (actor.org_id,)
                )
            connection.execute(
                "DELETE FROM sso_identities WHERE org_id = ? AND protocol IN ('oidc', 'saml')",
                (actor.org_id,),
            )
            self.app.audit.record(
                actor.org_id, "sso.remove", actor=actor, connection=connection
            )
        self.app.secrets.delete(actor.org_id, "sso_oidc_secret")

    def discover(self, email: str) -> dict[str, Any]:
        """Does this email address belong to an organization that signs in with SSO? Nothing more is revealed."""
        try:
            email = crypto.normalize_email(email)
        except ValidationFailed:
            return {"sso": False}
        domain = email.rsplit("@", 1)[1]
        with self.db.read() as connection:
            rows = many(
                connection,
                """SELECT c.org_id, c.protocol, c.domains, o.slug FROM sso_configs c JOIN organizations o ON o.org_id = c.org_id
                WHERE c.protocol IN ('oidc', 'saml') AND o.deleted_at IS NULL AND o.status = 'active' ORDER BY o.created_at""",
            )
        for row in rows:
            if domain in loads(row["domains"], []):
                try:
                    if not self.app.entitlements.resolve(row["org_id"]).has("auth.sso"):
                        continue
                except NotFound:
                    continue
                return {
                    "sso": True,
                    "org_slug": row["slug"],
                    "protocol": row["protocol"],
                }
        return {"sso": False}

    # ------------------------------------------------------------------ shared sign-in

    def _org_by_slug(self, slug: str) -> dict[str, Any]:
        with self.db.read() as connection:
            org = one(
                connection,
                "SELECT org_id, name, slug, status FROM organizations WHERE slug = ? AND deleted_at IS NULL",
                (slug,),
            )
        if org is None:
            raise NotFound("Single sign-on is not set up for this organization.")
        return org

    def _config_for(
        self, slug: str, protocol: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        org = self._org_by_slug(slug)
        config = self.get_config(org["org_id"])
        if not config or config["protocol"] != protocol:
            raise NotFound("Single sign-on is not set up for this organization.")
        if org["status"] != "active":
            raise Forbidden(
                "This organization is suspended. Contact your administrator."
            )
        self.app.entitlements.require_feature(
            org["org_id"], "auth.sso", label="Single sign-on"
        )
        return org, config

    def _store_state(
        self,
        org_id: str,
        protocol: str,
        *,
        state: str,
        nonce: str = "",
        verifier: str = "",
    ) -> None:
        with self.db.write() as connection:
            connection.execute(
                "DELETE FROM sso_states WHERE expires_at < ?", (self.now,)
            )
            connection.execute(
                "INSERT INTO sso_states (state, org_id, protocol, nonce, verifier, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    state,
                    org_id,
                    protocol,
                    nonce,
                    verifier,
                    self.now,
                    self.now + STATE_TTL,
                ),
            )

    def _consume_state(self, state: str, protocol: str) -> dict[str, Any]:
        """One-time: the row is deleted as it is read, so a replayed callback finds nothing."""
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT * FROM sso_states WHERE state = ? AND protocol = ?",
                (state or "", protocol),
            )
            if row is None:
                raise Unauthorized(
                    "This sign-in has expired or was already used. Start again.",
                    code="sso_state",
                )
            connection.execute("DELETE FROM sso_states WHERE state = ?", (state,))
        if row["expires_at"] < self.now:
            raise Unauthorized(
                "This sign-in has expired. Start again.", code="sso_state"
            )
        return row

    def _role_from_groups(
        self, group_map: dict[str, str], groups: list[str] | None
    ) -> str | None:
        if not group_map or groups is None:
            return None
        matched = {group_map[g] for g in groups if g in group_map}
        for role in ROLE_PRIORITY:
            if role in matched:
                return role
        return None

    def _synthetic_owner(self, org_id: str, label: str) -> Principal:
        """Actions the identity provider takes run as an owner named after it, so audit shows what caused them."""
        return Principal(
            kind="member",
            org_id=org_id,
            role=OWNER,
            member_id=label,
            email=f"{label}@system.invalid",
            name=label,
        )

    def _apply_role(
        self, org_id: str, member: dict[str, Any], role: str | None, label: str
    ) -> None:
        if role is None or role == member["role"] or member["role"] == OWNER:
            return
        if CANDIDATE in (member["role"], role):
            logger.info(
                "Not changing %s between candidate and staff via the identity provider",
                member["member_id"],
            )
            return
        self.app.tenancy.change_role(
            self._synthetic_owner(org_id, label), member["member_id"], role
        )

    def complete_login(
        self,
        org: dict[str, Any],
        config: dict[str, Any],
        *,
        protocol: str,
        subject: str,
        email: str,
        name: str = "",
        groups: list[str] | None = None,
        ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        org_id = org["org_id"]
        try:
            email = crypto.normalize_email(email)
        except ValidationFailed:
            raise Unauthorized(
                "The identity provider did not give an email address.",
                code="sso_no_email",
            ) from None
        subject = str(subject or "").strip()
        if not subject:
            raise Unauthorized(
                "The identity provider did not identify you.", code="sso_no_subject"
            )
        self.app.entitlements.require_state(org_id)
        member, event = None, ""
        with self.db.read() as connection:
            linked = one(
                connection,
                """SELECT m.* FROM sso_identities i JOIN members m ON m.member_id = i.member_id AND m.org_id = i.org_id
                WHERE i.org_id = ? AND i.protocol = ? AND i.subject = ?""",
                (org_id, protocol, subject),
            )
            by_email = one(
                connection,
                "SELECT * FROM members WHERE org_id = ? AND email = ? AND removed_at IS NULL",
                (org_id, email),
            )
        if linked is not None:
            member = linked
        elif by_email is not None:
            member, event = by_email, "member.sso_link"
        else:
            member, event = (
                self._provision(org_id, config, protocol, subject, email, name, groups),
                "member.sso_provision",
            )
        if member["removed_at"] or member["status"] != "active":
            raise Forbidden(
                "This account is not active. Contact your administrator.",
                code="inactive",
            )
        if event == "member.sso_link":
            with self.db.write() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO sso_identities (org_id, protocol, subject, member_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (org_id, protocol, subject, member["member_id"], self.now),
                )
                if not member["email_verified_at"]:
                    connection.execute(
                        "UPDATE members SET email_verified_at = ? WHERE member_id = ?",
                        (self.now, member["member_id"]),
                    )
                self.app.audit.record(
                    org_id,
                    "member.sso_link",
                    actor_type="member",
                    actor_id=member["member_id"],
                    actor_label=f"{member['role']}:{email}",
                    actor_role=member["role"],
                    target_type="member",
                    target_id=member["member_id"],
                    detail={"protocol": protocol},
                    connection=connection,
                )
        if name and not member["name"]:
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE members SET name = ? WHERE member_id = ?",
                    (name[:120], member["member_id"]),
                )
        self._apply_role(
            org_id,
            member,
            self._role_from_groups(config["group_role_map"], groups),
            f"sso-{protocol}",
        )
        token = self.app.identity.create_session_for(
            org_id, member["member_id"], ip=ip, user_agent=user_agent, auth_method="sso"
        )
        self.app.audit.record(
            org_id,
            "auth.sso_login",
            actor_type="member",
            actor_id=member["member_id"],
            actor_label=f"{member['role']}:{email}",
            actor_role=member["role"],
            target_type="member",
            target_id=member["member_id"],
            detail={"protocol": protocol},
            source="sso",
        )
        return {"token": token, "member_id": member["member_id"], "org_id": org_id}

    def _provision(
        self,
        org_id: str,
        config: dict[str, Any],
        protocol: str,
        subject: str,
        email: str,
        name: str,
        groups: list[str] | None,
    ) -> dict[str, Any]:
        domain = email.rsplit("@", 1)[1]
        if domain not in config["domains"]:
            raise Forbidden(
                "Your email domain is not allowed to join this organization. Ask an administrator for an invitation.",
                code="domain_not_allowed",
            )
        role = (
            self._role_from_groups(config["group_role_map"], groups)
            or config["default_role"]
        )
        if role == OWNER:  # unreachable by validation; kept as a guard
            role = CANDIDATE
        self.app.entitlements.require_state(org_id, write=True)
        self.app.tenancy.require_seat(org_id, role)
        member_id = new_id()
        try:
            with self.db.write() as connection:
                connection.execute(
                    """INSERT INTO members (member_id, org_id, email, name, password_hash, role, status, email_verified_at, invited_by, created_at)
                    VALUES (?, ?, ?, ?, '', ?, 'active', ?, 'sso', ?)""",
                    (member_id, org_id, email, name[:120], role, self.now, self.now),
                )
                if role == CANDIDATE:
                    self.app.workspaces.create(org_id, member_id, connection=connection)
                connection.execute(
                    "INSERT INTO sso_identities (org_id, protocol, subject, member_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (org_id, protocol, subject, member_id, self.now),
                )
                self.app.audit.record(
                    org_id,
                    "member.sso_provision",
                    actor_type="system",
                    actor_label=f"sso:{protocol}",
                    target_type="member",
                    target_id=member_id,
                    detail={"protocol": protocol, "role": role},
                    source="sso",
                    connection=connection,
                )
        except INTEGRITY_ERRORS:
            raise Conflict("This person is already a member.") from None
        with self.db.read() as connection:
            created = one(
                connection, "SELECT * FROM members WHERE member_id = ?", (member_id,)
            )
        if created is None:
            raise NotFound("Member not found.")
        return created

    # ------------------------------------------------------------------ OIDC

    def oidc_start(self, org_slug: str) -> str:
        org, config = self._config_for(org_slug, "oidc")
        settings = config["config"]
        state, nonce, verifier = (
            secrets.token_urlsafe(24),
            secrets.token_urlsafe(24),
            secrets.token_urlsafe(48),
        )
        self._store_state(
            org["org_id"], "oidc", state=state, nonce=nonce, verifier=verifier
        )
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        query = urlencode(
            {
                "response_type": "code",
                "client_id": settings["client_id"],
                "redirect_uri": self.sp_details(org["org_id"])["oidc_redirect_uri"],
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        endpoint = settings["authorization_endpoint"]
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"

    def _jwks_for(self, url: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        cached = self._jwks.get(url)
        if cached and cached[0] > self.now and not refresh:
            return cached[1]
        keys = list(_fetch(url).get("keys") or [])
        self._jwks[url] = (self.now + JWKS_TTL, keys)
        return keys

    def _verify_id_token(
        self, id_token: str, settings: dict[str, Any], nonce: str
    ) -> dict[str, Any]:
        import jwt
        from jwt.algorithms import ECAlgorithm, RSAAlgorithm

        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise Unauthorized(
                "The identity provider's token could not be read.", code="sso_token"
            ) from None
        if header.get("alg") not in {"RS256", "RS384", "RS512", "ES256", "ES384"}:
            raise Unauthorized(
                "The identity provider signed the token with an unsupported algorithm.",
                code="sso_token",
            )
        key_data = None
        for refresh in (False, True):
            for candidate in self._jwks_for(settings["jwks_uri"], refresh=refresh):
                if candidate.get("kid") == header.get("kid") or (
                    not header.get("kid")
                    and len(self._jwks_for(settings["jwks_uri"])) == 1
                ):
                    key_data = candidate
                    break
            if key_data:
                break
        if key_data is None:
            raise Unauthorized(
                "The token was signed with an unknown key.", code="sso_token"
            )
        try:
            public: Any = (
                ECAlgorithm if key_data.get("kty") == "EC" else RSAAlgorithm
            ).from_jwk(json.dumps(key_data))
            # Expiry is checked against the application clock below, so the library only checks signature and structure.
            claims = jwt.decode(
                id_token,
                public,
                algorithms=[header["alg"]],
                audience=settings["client_id"],
                issuer=settings["issuer"],
                options={
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                    "require": ["exp", "iss", "aud", "sub"],
                },
            )
        except jwt.PyJWTError as error:
            raise Unauthorized(
                f"The identity provider's token was rejected ({type(error).__name__}).",
                code="sso_token",
            ) from None
        if int(claims["exp"]) + SAML_SKEW < self.now:
            raise Unauthorized(
                "The identity provider's token has expired.", code="sso_token"
            )
        if claims.get("nbf") and int(claims["nbf"]) - SAML_SKEW > self.now:
            raise Unauthorized(
                "The identity provider's token is not valid yet.", code="sso_token"
            )
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise Unauthorized(
                "The sign-in response does not match the request (nonce).",
                code="sso_token",
            )
        return claims  # type: ignore[no-any-return]

    def oidc_callback(
        self, code: str, state: str, *, ip: str = "", user_agent: str = ""
    ) -> dict[str, Any]:
        stored = self._consume_state(state, "oidc")
        org = self.app.tenancy.org(stored["org_id"])
        _, config = self._config_for(org["slug"], "oidc")
        settings = config["config"]
        if not code:
            raise Unauthorized(
                "The identity provider returned no authorization code.", code="sso_code"
            )
        secret = self.app.secrets.get(org["org_id"], "sso_oidc_secret") or ""
        tokens = _fetch(
            settings["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.sp_details(org["org_id"])["oidc_redirect_uri"],
                "client_id": settings["client_id"],
                "client_secret": secret,
                "code_verifier": stored["verifier"],
            },
        )
        id_token = tokens.get("id_token")
        if not id_token:
            raise Unauthorized(
                "The identity provider returned no ID token.", code="sso_token"
            )
        claims = self._verify_id_token(str(id_token), settings, stored["nonce"])
        if claims.get("email_verified") in (False, "false", "False", 0):
            raise Unauthorized(
                "The identity provider has not verified your email address.",
                code="sso_email_unverified",
            )
        raw_groups = claims.get(settings.get("groups_claim") or "groups")
        groups = (
            None
            if raw_groups is None
            else (
                [raw_groups]
                if isinstance(raw_groups, str)
                else [str(g) for g in raw_groups]
            )
        )
        return self.complete_login(
            org,
            config,
            protocol="oidc",
            subject=str(claims["sub"]),
            email=str(claims.get("email", "")),
            name=str(claims.get("name", "")),
            groups=groups,
            ip=ip,
            user_agent=user_agent,
        )

    # ------------------------------------------------------------------ SAML

    def saml_metadata(self, org_slug: str, *, public: bool = True) -> str:
        """The service provider metadata. The public URL answers only once SAML is configured (a 404 otherwise, so it
        reveals nothing about an organization); administrators fetch it earlier through the authenticated route."""
        if public:
            org, _config = self._config_for(org_slug, "saml")
        else:
            org = self._org_by_slug(org_slug)
        details = self.sp_details(org["org_id"])
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{escape(details["saml_entity_id"], {chr(34): "&quot;"})}">'
            '<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
            "<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>"
            '<md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
            f'Location="{escape(details["saml_acs_url"], {chr(34): "&quot;"})}" index="0" isDefault="true"/>'
            "</md:SPSSODescriptor></md:EntityDescriptor>"
        )

    def saml_start(self, org_slug: str) -> str:
        org, config = self._config_for(org_slug, "saml")
        settings = config["config"]
        details = self.sp_details(org["org_id"])
        request_id = "_" + secrets.token_hex(16)
        relay = secrets.token_urlsafe(24)
        issued = _iso(self.now)
        xml = (
            f'<samlp:AuthnRequest xmlns:samlp="{SAMLP_NS}" xmlns:saml="{SAML_NS}" ID="{request_id}" Version="2.0" IssueInstant="{issued}" '
            f'Destination="{escape(settings["sso_url"], {chr(34): "&quot;"})}" ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
            f'AssertionConsumerServiceURL="{escape(details["saml_acs_url"], {chr(34): "&quot;"})}">'
            f"<saml:Issuer>{escape(details['saml_entity_id'])}</saml:Issuer>"
            '<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" AllowCreate="true"/></samlp:AuthnRequest>'
        )
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        payload = base64.b64encode(
            compressor.compress(xml.encode()) + compressor.flush()
        ).decode()
        self._store_state(org["org_id"], "saml", state=relay, nonce=request_id)
        url = settings["sso_url"]
        return f"{url}{'&' if '?' in url else '?'}SAMLRequest={quote(payload, safe='')}&RelayState={quote(relay, safe='')}"

    def _parse_xml(self, raw: bytes) -> Any:
        etree = importlib.import_module("lxml.etree")

        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise Unauthorized(
                "The SAML response is not acceptable.", code="saml_malformed"
            )
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            dtd_validation=False,
            huge_tree=False,
            remove_comments=True,
        )
        try:
            return etree.fromstring(raw, parser)
        except etree.XMLSyntaxError:
            raise Unauthorized(
                "The SAML response could not be read.", code="saml_malformed"
            ) from None

    def _verified_element(self, root: Any, certificates: list[str]) -> Any:
        """The Response or an Assertion whose signature verifies against the stored certificate.

        Only the element signxml returns is trusted from here on: anything else in the document, such as an
        extra unsigned assertion, is ignored, which is what defeats signature-wrapping.
        """
        from signxml import SignatureConfiguration, XMLVerifier

        candidates = [root, *root.findall(f"{{{SAML_NS}}}Assertion")]
        for element in candidates:
            if element.find(f"{{{DS_NS}}}Signature") is None:
                continue
            for certificate in certificates:
                try:
                    result = XMLVerifier().verify(
                        copy.deepcopy(element),
                        x509_cert=certificate,
                        id_attribute="ID",
                        expect_config=SignatureConfiguration(
                            location="./", expect_references=1, require_x509=False
                        ),
                    )
                except Exception:  # noqa: BLE001 - any failure to verify is a rejection
                    continue
                verified: Any = result[0] if isinstance(result, list) else result
                return verified.signed_xml
        raise Unauthorized(
            "The SAML response is not signed by your identity provider.",
            code="saml_signature",
        )

    def _check_assertion(
        self,
        assertion: Any,
        response: Any,
        settings: dict[str, Any],
        details: dict[str, str],
        request_id: str,
    ) -> None:
        ns = {"saml": SAML_NS, "samlp": SAMLP_NS}
        now = self.now

        def fail(message: str) -> None:
            raise Unauthorized(message, code="saml_invalid")

        issuer = assertion.findtext("saml:Issuer", namespaces=ns)
        if (issuer or "").strip() != settings["idp_entity_id"]:
            fail("The SAML assertion was issued by a different provider.")
        conditions = assertion.find("saml:Conditions", ns)
        if conditions is None:
            fail("The SAML assertion has no conditions.")
        if (
            conditions.get("NotBefore")
            and _epoch(conditions.get("NotBefore")) - SAML_SKEW > now
        ):
            fail("The SAML assertion is not valid yet.")
        if (
            conditions.get("NotOnOrAfter") is None
            or _epoch(conditions.get("NotOnOrAfter")) + SAML_SKEW <= now
        ):
            fail("The SAML assertion has expired.")
        audiences = [
            a.text.strip()
            for a in conditions.findall("saml:AudienceRestriction/saml:Audience", ns)
            if a.text
        ]
        if details["saml_entity_id"] not in audiences:
            fail("The SAML assertion is for a different service.")
        confirmations = [
            c
            for c in assertion.findall("saml:Subject/saml:SubjectConfirmation", ns)
            if c.get("Method") == "urn:oasis:names:tc:SAML:2.0:cm:bearer"
        ]
        valid = False
        for confirmation in confirmations:
            data = confirmation.find("saml:SubjectConfirmationData", ns)
            if data is None:
                continue
            if data.get("Recipient") != details["saml_acs_url"]:
                continue
            if (
                data.get("NotOnOrAfter") is None
                or _epoch(data.get("NotOnOrAfter")) + SAML_SKEW <= now
            ):
                continue
            if data.get("InResponseTo") != request_id:
                continue
            valid = True
        if not valid:
            fail(
                "The SAML assertion does not confirm this sign-in (recipient, time or request)."
            )
        if response is not None:
            destination = response.get("Destination")
            if destination and destination != details["saml_acs_url"]:
                fail("The SAML response was sent to a different address.")
            if (
                response.get("InResponseTo")
                and response.get("InResponseTo") != request_id
            ):
                fail("The SAML response answers a different request.")
            status = response.find("samlp:Status/samlp:StatusCode", ns)
            if status is not None and not (status.get("Value") or "").endswith(
                ":Success"
            ):
                fail("The identity provider reported a failed sign-in.")

    def saml_acs(
        self,
        org_slug: str,
        saml_response_b64: str,
        relay_state: str,
        *,
        ip: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        org, config = self._config_for(org_slug, "saml")
        settings = config["config"]
        details = self.sp_details(org["org_id"])
        if len(saml_response_b64 or "") > MAX_SAML_BYTES:
            raise Unauthorized("The SAML response is too large.", code="saml_malformed")
        try:
            raw = base64.b64decode(saml_response_b64, validate=False)
        except ValueError:
            raise Unauthorized(
                "The SAML response could not be decoded.", code="saml_malformed"
            ) from None
        stored = self._consume_state(relay_state, "saml")
        if stored["org_id"] != org["org_id"]:
            raise Unauthorized(
                "This sign-in belongs to a different organization.", code="sso_state"
            )
        root = self._parse_xml(raw)
        verified = self._verified_element(
            root,
            re.findall(
                r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----",
                settings["idp_certificate"],
                flags=re.S,
            ),
        )
        # The Response's own attributes are checked whether or not it is signed: if it is not, an attacker can
        # only make us stricter by changing them, and when it is, they are authentic.
        response = root if root.tag == f"{{{SAMLP_NS}}}Response" else None
        if verified.tag == f"{{{SAMLP_NS}}}Response":
            response = verified
            assertions = verified.findall(f"{{{SAML_NS}}}Assertion")
            if len(assertions) != 1:
                raise Unauthorized(
                    "The signed SAML response must carry exactly one assertion.",
                    code="saml_invalid",
                )
            assertion = assertions[0]
        elif verified.tag == f"{{{SAML_NS}}}Assertion":
            assertion = verified
        else:
            raise Unauthorized(
                "The signed SAML element is not acceptable.", code="saml_invalid"
            )
        self._check_assertion(assertion, response, settings, details, stored["nonce"])
        assertion_id = assertion.get("ID") or ""
        if not assertion_id:
            raise Unauthorized("The SAML assertion has no id.", code="saml_invalid")
        try:  # an assertion is accepted once
            with self.db.write() as connection:
                connection.execute(
                    "INSERT INTO sso_states (state, org_id, protocol, nonce, verifier, created_at, expires_at) VALUES (?, ?, 'saml-seen', '', '', ?, ?)",
                    (
                        f"seen:{org['org_id']}:{hashlib.sha256(assertion_id.encode()).hexdigest()}",
                        org["org_id"],
                        self.now,
                        self.now + 3600,
                    ),
                )
        except INTEGRITY_ERRORS:
            raise Unauthorized(
                "This SAML assertion was already used.", code="saml_replay"
            ) from None
        ns = {"saml": SAML_NS}
        name_id = (
            assertion.findtext("saml:Subject/saml:NameID", namespaces=ns) or ""
        ).strip()
        attributes: dict[str, list[str]] = {}
        for attribute in assertion.findall(
            "saml:AttributeStatement/saml:Attribute", ns
        ):
            key = (attribute.get("Name") or "").strip()
            values = [
                (v.text or "").strip()
                for v in attribute.findall("saml:AttributeValue", ns)
                if (v.text or "").strip()
            ]
            if key:
                attributes.setdefault(key, []).extend(values)
        lowered = {k.lower(): v for k, v in attributes.items()}
        mapping = settings.get("attribute_map") or {}

        def pick(kind: str, defaults: tuple[str, ...]) -> list[str] | None:
            if kind in mapping:
                return attributes.get(mapping[kind])
            for key in defaults:
                if key.lower() in lowered:
                    return lowered[key.lower()]
            return None

        email_values = pick("email", EMAIL_ATTRIBUTES)
        email = (email_values or [""])[0] or (name_id if "@" in name_id else "")
        name = (pick("name", NAME_ATTRIBUTES) or [""])[0]
        groups = pick("groups", GROUP_ATTRIBUTES)
        return self.complete_login(
            org,
            config,
            protocol="saml",
            subject=name_id or email,
            email=email,
            name=name,
            groups=groups,
            ip=ip,
            user_agent=user_agent,
        )

    # ------------------------------------------------------------------ SCIM tokens

    def _require_scim_manage(self, actor: Principal) -> None:
        self.app.authz.require(actor, "sso.manage")
        self.app.entitlements.require_state(actor.org_id, write=True)
        self.app.entitlements.require_feature(
            actor.org_id, "auth.scim", label="SCIM provisioning"
        )

    def create_scim_token(self, actor: Principal, label: str = "") -> dict[str, Any]:
        self._require_scim_manage(actor)
        token = "scim_" + secrets.token_urlsafe(32)
        digest = crypto.hash_token(token)
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO scim_tokens (token_hash, org_id, label, created_at) VALUES (?, ?, ?, ?)",
                (digest, actor.org_id, str(label or "")[:80], self.now),
            )
            self.app.audit.record(
                actor.org_id,
                "sso.scim_token_create",
                actor=actor,
                target_type="scim_token",
                target_id=digest[:16],
                connection=connection,
            )
        return {
            "id": digest[:16],
            "token": token,
            "label": str(label or "")[:80],
            "base_url": self.sp_details(actor.org_id)["scim_base_url"],
        }

    def list_scim_tokens(self, actor: Principal) -> list[dict[str, Any]]:
        self.app.authz.require(actor, "sso.manage")
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT token_hash, label, created_at, last_used_at, revoked_at FROM scim_tokens WHERE org_id = ? ORDER BY created_at DESC",
                (actor.org_id,),
            )
        return [
            {
                "id": r["token_hash"][:16],
                "label": r["label"],
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
                "revoked_at": r["revoked_at"],
            }
            for r in rows
        ]

    def revoke_scim_token(self, actor: Principal, token_id: str) -> None:
        self.app.authz.require(actor, "sso.manage")
        with self.db.write() as connection:
            changed = connection.execute(
                "UPDATE scim_tokens SET revoked_at = ? WHERE org_id = ? AND substr(token_hash, 1, 16) = ? AND revoked_at IS NULL",
                (self.now, actor.org_id, token_id),
            ).rowcount
            if not changed:
                raise NotFound("Token not found.")
            self.app.audit.record(
                actor.org_id,
                "sso.scim_token_revoke",
                actor=actor,
                target_type="scim_token",
                target_id=token_id,
                connection=connection,
            )

    def scim_authenticate(self, authorization: str) -> str:
        """The organization a bearer token belongs to, or Unauthorized."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise Unauthorized("A bearer token is required.", code="scim_auth")
        digest = crypto.hash_token(token.strip())
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT org_id, revoked_at FROM scim_tokens WHERE token_hash = ?",
                (digest,),
            )
            if row is None or row["revoked_at"]:
                raise Unauthorized("The token is not valid.", code="scim_auth")
            connection.execute(
                "UPDATE scim_tokens SET last_used_at = ? WHERE token_hash = ?",
                (self.now, digest),
            )
        org_id = row["org_id"]
        try:
            self.app.tenancy.org(org_id)
            self.app.entitlements.require_feature(
                org_id, "auth.scim", label="SCIM provisioning"
            )
        except NotFound:
            raise Unauthorized("The token is not valid.", code="scim_auth") from None
        return str(org_id)

    # ------------------------------------------------------------------ SCIM users

    def _scim_actor(self, org_id: str) -> Principal:
        return self._synthetic_owner(org_id, "scim")

    @staticmethod
    def _scim_name(name: str) -> dict[str, str]:
        parts = name.split(" ", 1)
        return {
            "formatted": name,
            "givenName": parts[0] if name else "",
            "familyName": parts[1] if len(parts) > 1 else "",
        }

    def scim_user(self, org_id: str, member: dict[str, Any]) -> dict[str, Any]:
        base = self.sp_details(org_id)["scim_base_url"]
        return {
            "schemas": [SCIM_USER],
            "id": member["member_id"],
            "userName": member["email"],
            "name": self._scim_name(member["name"] or ""),
            "displayName": member["name"] or member["email"],
            "emails": [{"value": member["email"], "primary": True, "type": "work"}],
            "active": member["status"] == "active" and not member["removed_at"],
            "roles": [{"value": member["role"], "primary": True}],
            "meta": {
                "resourceType": "User",
                "created": _iso(member["created_at"]),
                "location": f"{base}/Users/{member['member_id']}",
            },
        }

    def scim_list_users(
        self, org_id: str, *, filter_text: str = "", start: int = 1, count: int = 100
    ) -> dict[str, Any]:
        clauses, params = ["org_id = ?", "removed_at IS NULL"], [org_id]
        if filter_text:
            match = re.fullmatch(
                r'\s*(userName|emails\.value|externalId|id)\s+eq\s+"([^"]{1,254})"\s*',
                filter_text,
                flags=re.I,
            )
            if not match:
                raise ValidationFailed(
                    "Only 'userName eq \"value\"' style filters are supported.",
                    scim_type="invalidFilter",
                )
            column = {
                "username": "email",
                "emails.value": "email",
                "externalid": "external_id",
                "id": "member_id",
            }[match.group(1).lower()]
            clauses.append(f"{column} = ?")
            params.append(
                match.group(2).lower() if column == "email" else match.group(2)
            )
        where = " AND ".join(clauses)
        start, count = max(1, int(start)), max(0, min(int(count), 200))
        with self.db.read() as connection:
            counted = one(
                connection, f"SELECT COUNT(*) AS n FROM members WHERE {where}", params
            )
            total = counted["n"] if counted else 0
            rows = many(
                connection,
                f"SELECT * FROM members WHERE {where} ORDER BY created_at, member_id LIMIT ? OFFSET ?",
                (*params, count, start - 1),
            )
        return {
            "schemas": [SCIM_LIST],
            "totalResults": int(total),
            "startIndex": start,
            "itemsPerPage": len(rows),
            "Resources": [self.scim_user(org_id, r) for r in rows],
        }

    def _scim_member(self, org_id: str, member_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT * FROM members WHERE member_id = ? AND org_id = ? AND removed_at IS NULL",
                (member_id, org_id),
            )
        if row is None:
            raise NotFound("User not found.")
        return row

    @staticmethod
    def _scim_email(body: dict[str, Any]) -> str:
        email = str(body.get("userName") or "").strip()
        for item in body.get("emails") or []:
            if isinstance(item, dict) and item.get("primary") and item.get("value"):
                email = email or str(item["value"])
        return email

    @staticmethod
    def _scim_display(body: dict[str, Any]) -> str:
        raw_name = body.get("name")
        name: dict[str, Any] = raw_name if isinstance(raw_name, dict) else {}
        full = str(
            name.get("formatted")
            or " ".join(p for p in (name.get("givenName"), name.get("familyName")) if p)
            or body.get("displayName")
            or ""
        ).strip()
        return full[:120]

    def scim_create_user(self, org_id: str, body: dict[str, Any]) -> dict[str, Any]:
        email = crypto.normalize_email(self._scim_email(body))
        with self.db.read() as connection:
            if one(
                connection,
                "SELECT 1 AS x FROM members WHERE org_id = ? AND email = ? AND removed_at IS NULL",
                (org_id, email),
            ):
                raise Conflict(
                    "A user with this userName exists.", scim_type="uniqueness"
                )
        config = self.get_config(org_id)
        role = (config or {}).get("default_role") or CANDIDATE
        if role == OWNER:
            role = CANDIDATE
        self.app.entitlements.require_state(org_id, write=True)
        self.app.tenancy.require_seat(org_id, role)
        member_id = new_id()
        active = body.get("active", True) not in (False, "false", "False")
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO members (member_id, org_id, email, name, password_hash, role, status, email_verified_at, invited_by, created_at, external_id)
                VALUES (?, ?, ?, ?, '', ?, 'active', ?, 'scim', ?, ?)""",
                (
                    member_id,
                    org_id,
                    email,
                    self._scim_display(body),
                    role,
                    self.now,
                    self.now,
                    str(body.get("externalId") or "")[:200],
                ),
            )
            if role == CANDIDATE:
                self.app.workspaces.create(org_id, member_id, connection=connection)
            self.app.audit.record(
                org_id,
                "member.scim_provision",
                actor_type="system",
                actor_label="scim",
                target_type="member",
                target_id=member_id,
                detail={"role": role},
                source="scim",
                connection=connection,
            )
        if not active:
            self.scim_deactivate(org_id, member_id)
            return self.scim_user(org_id, self._removed_row(org_id, member_id))
        return self.scim_user(org_id, self._scim_member(org_id, member_id))

    def _removed_row(self, org_id: str, member_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            return (
                one(
                    connection,
                    "SELECT * FROM members WHERE member_id = ? AND org_id = ?",
                    (member_id, org_id),
                )
                or {}
            )

    def scim_deactivate(self, org_id: str, member_id: str) -> None:
        self._scim_member(org_id, member_id)
        self.app.tenancy.remove_member(self._scim_actor(org_id), member_id)
        self.app.audit.record(
            org_id,
            "member.scim_deprovision",
            actor_type="system",
            actor_label="scim",
            target_type="member",
            target_id=member_id,
            source="scim",
        )

    def _scim_apply(
        self, org_id: str, member: dict[str, Any], changes: dict[str, Any]
    ) -> bool:
        """Apply attribute changes; returns False if the member was deactivated."""
        if "name" in changes:
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE members SET name = ? WHERE member_id = ?",
                    (changes["name"][:120], member["member_id"]),
                )
        if "externalId" in changes:
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE members SET external_id = ? WHERE member_id = ?",
                    (str(changes["externalId"])[:200], member["member_id"]),
                )
        if (
            "userName" in changes
            and changes["userName"].strip().lower() != member["email"]
        ):
            if member["role"] in {"owner", "admin"}:
                # An email change is a password-reset takeover: provisioning must not be able to
                # reroute the account of someone who administers the organization.
                raise Forbidden(
                    "Owner and admin sign-in addresses are changed in Monosage, not through provisioning.",
                    scim_type="mutability",
                )
            email = crypto.normalize_email(changes["userName"])
            with self.db.write() as connection:
                if one(
                    connection,
                    "SELECT 1 AS x FROM members WHERE org_id = ? AND email = ? AND removed_at IS NULL",
                    (org_id, email),
                ):
                    raise Conflict(
                        "A user with this userName exists.", scim_type="uniqueness"
                    )
                connection.execute(
                    "UPDATE members SET email = ? WHERE member_id = ?",
                    (email, member["member_id"]),
                )
        if changes.get("active") is False:
            self.scim_deactivate(org_id, member["member_id"])
            return False
        return True

    @staticmethod
    def _bool(value: Any) -> bool:
        return value not in (False, "false", "False", "FALSE", 0, "0")

    def scim_replace_user(
        self, org_id: str, member_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        member = self._scim_member(org_id, member_id)
        changes: dict[str, Any] = {}
        if body.get("userName"):
            changes["userName"] = str(body["userName"])
        if "name" in body or "displayName" in body:
            changes["name"] = self._scim_display(body)
        if "externalId" in body:
            changes["externalId"] = body["externalId"]
        if "active" in body:
            changes["active"] = self._bool(body["active"])
        if not self._scim_apply(org_id, member, changes):
            return self.scim_user(org_id, self._removed_row(org_id, member_id))
        return self.scim_user(org_id, self._scim_member(org_id, member_id))

    def scim_patch_user(
        self, org_id: str, member_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        member = self._scim_member(org_id, member_id)
        operations = body.get("Operations") or body.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValidationFailed(
                "Send an Operations list.", scim_type="invalidSyntax"
            )
        changes: dict[str, Any] = {}
        for operation in operations:
            op = str(operation.get("op", "")).lower()
            path = str(operation.get("path", "")).strip()
            value = operation.get("value")
            if op not in {"add", "replace", "remove"}:
                raise ValidationFailed(
                    "Unknown patch operation.", scim_type="invalidSyntax"
                )
            values = (
                {path: value} if path else (value if isinstance(value, dict) else {})
            )
            for key, val in values.items():
                key = str(key)
                if key == "active":
                    changes["active"] = self._bool(val)
                elif key in {"userName", "externalId"}:
                    changes[key] = val
                elif key in {"displayName", "name.formatted"}:
                    changes["name"] = str(val or "")
                elif key == "name" and isinstance(val, dict):
                    changes["name"] = self._scim_display({"name": val})
                elif key in {"name.givenName", "name.familyName"}:
                    parts = self._scim_name(member["name"] or "")
                    parts[
                        "givenName" if key.endswith("givenName") else "familyName"
                    ] = str(val or "")
                    changes["name"] = (
                        f"{parts['givenName']} {parts['familyName']}".strip()
                    )
        if not self._scim_apply(org_id, member, changes):
            return self.scim_user(org_id, self._removed_row(org_id, member_id))
        return self.scim_user(org_id, self._scim_member(org_id, member_id))

    # ------------------------------------------------------------------ SCIM groups

    def _scim_groups(self, org_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        config = self.get_config(org_id)
        stored = (config["config"].get("scim_groups") if config else None) or {}
        return config or {}, stored

    def _save_scim_groups(self, org_id: str, groups: dict[str, Any]) -> None:
        config = self.get_config(org_id)
        with self.db.write() as connection:
            if config is None:
                connection.execute(
                    "INSERT INTO sso_configs (org_id, protocol, config, enforced, domains, group_role_map, default_role, updated_at) VALUES (?, 'none', ?, 0, '[]', '{}', 'candidate', ?)",
                    (org_id, dumps({"scim_groups": groups}), self.now),
                )
            else:
                settings = dict(config["config"])
                settings["scim_groups"] = groups
                connection.execute(
                    "UPDATE sso_configs SET config = ?, updated_at = ? WHERE org_id = ?",
                    (dumps(settings), self.now, org_id),
                )

    def scim_group(
        self, org_id: str, group_id: str, group: dict[str, Any]
    ) -> dict[str, Any]:
        base = self.sp_details(org_id)["scim_base_url"]
        members = []
        for member_id in group.get("members", []):
            with self.db.read() as connection:
                row = one(
                    connection,
                    "SELECT email FROM members WHERE member_id = ? AND org_id = ? AND removed_at IS NULL",
                    (member_id, org_id),
                )
            if row:
                members.append(
                    {
                        "value": member_id,
                        "display": row["email"],
                        "$ref": f"{base}/Users/{member_id}",
                    }
                )
        return {
            "schemas": [SCIM_GROUP],
            "id": group_id,
            "displayName": group["displayName"],
            "members": members,
            "meta": {"resourceType": "Group", "location": f"{base}/Groups/{group_id}"},
        }

    def scim_list_groups(self, org_id: str) -> dict[str, Any]:
        _, groups = self._scim_groups(org_id)
        resources = [self.scim_group(org_id, gid, g) for gid, g in groups.items()]
        return {
            "schemas": [SCIM_LIST],
            "totalResults": len(resources),
            "startIndex": 1,
            "itemsPerPage": len(resources),
            "Resources": resources,
        }

    def scim_get_group(self, org_id: str, group_id: str) -> dict[str, Any]:
        _, groups = self._scim_groups(org_id)
        if group_id not in groups:
            raise NotFound("Group not found.")
        return self.scim_group(org_id, group_id, groups[group_id])

    def _sync_group_roles(self, org_id: str, member_ids: set[str]) -> None:
        config, groups = self._scim_groups(org_id)
        group_map = config.get("group_role_map") or {}
        for member_id in member_ids:
            try:
                member = self._scim_member(org_id, member_id)
            except NotFound:
                continue
            mine = [
                g["displayName"]
                for g in groups.values()
                if member_id in g.get("members", [])
            ]
            self._apply_role(
                org_id, member, self._role_from_groups(group_map, mine), "scim"
            )

    def scim_create_group(self, org_id: str, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("displayName") or "").strip()
        if not name or len(name) > 200:
            raise ValidationFailed("Send a displayName.", scim_type="invalidValue")
        _, groups = self._scim_groups(org_id)
        if any(g["displayName"] == name for g in groups.values()):
            raise Conflict(
                "A group with this displayName exists.", scim_type="uniqueness"
            )
        members = []
        for item in body.get("members") or []:
            member_id = str(item.get("value", ""))
            self._scim_member(org_id, member_id)
            members.append(member_id)
        group_id = new_id()
        groups[group_id] = {"displayName": name, "members": members}
        self._save_scim_groups(org_id, groups)
        self._sync_group_roles(org_id, set(members))
        return self.scim_get_group(org_id, group_id)

    def scim_patch_group(
        self, org_id: str, group_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        _, groups = self._scim_groups(org_id)
        if group_id not in groups:
            raise NotFound("Group not found.")
        group = groups[group_id]
        operations = body.get("Operations") or body.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValidationFailed(
                "Send an Operations list.", scim_type="invalidSyntax"
            )
        touched: set[str] = set(group.get("members", []))
        for operation in operations:
            op, path, value = (
                str(operation.get("op", "")).lower(),
                str(operation.get("path", "")),
                operation.get("value"),
            )
            if path == "displayName" and op in {"replace", "add"}:
                group["displayName"] = (
                    str(value or "").strip()[:200] or group["displayName"]
                )
            elif path.startswith("members") or (
                not path and isinstance(value, dict) and "members" in value
            ):
                items = value.get("members") if isinstance(value, dict) else value
                ids = [
                    str(i.get("value")) for i in (items or []) if isinstance(i, dict)
                ]
                match = re.fullmatch(r'members\[value eq "([^"]+)"\]', path)
                if match:
                    ids = [match.group(1)]
                if op == "add":
                    for member_id in ids:
                        self._scim_member(org_id, member_id)
                        if member_id not in group["members"]:
                            group["members"].append(member_id)
                elif op == "remove":
                    group["members"] = (
                        [m for m in group["members"] if m not in ids] if ids else []
                    )
                elif op == "replace":
                    for member_id in ids:
                        self._scim_member(org_id, member_id)
                    group["members"] = ids
                touched |= set(ids)
        self._save_scim_groups(org_id, groups)
        self._sync_group_roles(org_id, touched)
        return self.scim_get_group(org_id, group_id)

    def scim_delete_group(self, org_id: str, group_id: str) -> None:
        _, groups = self._scim_groups(org_id)
        if group_id not in groups:
            raise NotFound("Group not found.")
        touched = set(groups.pop(group_id).get("members", []))
        self._save_scim_groups(org_id, groups)
        self._sync_group_roles(org_id, touched)


__all__ = ["Sso", "ROLE_PRIORITY", "BILLING", "PlanRequired"]

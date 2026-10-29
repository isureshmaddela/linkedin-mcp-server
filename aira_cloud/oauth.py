"""OAuth 2.0 for third-party apps, and personal access tokens (the developer platform).

What an organization's admins and members can do:

* **Register an app** (``oauth_clients``): a name, exact redirect addresses (https, or http on the loopback for
  apps on a person's own computer), the scopes it may ask for, and whether it is *confidential* (has a secret,
  kept only as a hash and shown once) or *public* (a browser or phone app: no secret, PKCE required).
* **Authorization code + PKCE** (RFC 6749 / 7636): the app sends a person to ``/oauth/authorize``; the person
  signs in, sees the app's name and exactly what it asks for, and allows or denies. The code is single use, ten
  minutes, bound to the app, the redirect address, the PKCE challenge (S256 only) and the person.
* **Tokens**: access tokens last an hour; refresh tokens last thirty days and rotate on every use; presenting an
  old refresh token again revokes the whole family (theft detection). Tokens are opaque random strings kept
  only as SHA-256 hashes.
* **Client credentials**: a confidential app acting for the organization itself (server to server), limited to
  the business apps' scopes: never a person's own data (Careers, Health, Family...).
* **Personal access tokens**: a person makes a token for their own scripts or a no-code tool, choosing scopes
  and an expiry; it can do no more than they can.
* **Scopes** are per app: ``<app>:read`` (GET) and ``<app>:write`` (everything else), plus ``hub:read`` /
  ``hub:write`` for the Business hub and ``profile`` for /api/oauth/userinfo. A token reaches only the routes of
  the apps it holds a scope for; account, sign-in, organization and platform routes never accept a token.

Revocation (RFC 7009), introspection (RFC 7662) and server metadata (RFC 8414) are in routes/developers.py.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit

from aira import apps as app_catalog
from aira_cloud import crypto
from aira_cloud.db import dumps, loads, many, new_id, one, platform_scope
from aira_cloud.errors import (
    Conflict,
    Forbidden,
    NotFound,
    ValidationFailed,
)
from aira_cloud.rbac import ADMIN, Principal

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86_400
CODE_TTL = 600
PAT_DAYS = (7, 30, 90, 365)
MAX_CLIENTS = 25
MAX_PATS = 25
GRANTS = ("authorization_code", "refresh_token", "client_credentials")
# A person's own data: never available to an app acting for the organization (client credentials).
PERSONAL_APPS = frozenset({"careers"} | {app.id for app in app_catalog.LIFE_APPS})


class OAuthError(Exception):
    """An error in the OAuth wire format (RFC 6749 section 5.2)."""

    def __init__(self, error: str, description: str, status: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status

    def payload(self) -> dict[str, str]:
        return {"error": self.error, "error_description": self.description}


# ---------------------------------------------------------------------------
# scopes
# ---------------------------------------------------------------------------


def scope_catalog() -> list[dict[str, Any]]:
    """Every scope an app can ask for, with words a person understands on the consent screen."""
    out = [
        {
            "scope": "profile",
            "app": "",
            "label": "Your name and email address",
            "personal": True,
        }
    ]
    for app in app_catalog.APPS:
        personal = app.id in PERSONAL_APPS
        out.append(
            {
                "scope": f"{app.id}:read",
                "app": app.id,
                "label": f"See your {app.name} data",
                "personal": personal,
            }
        )
        out.append(
            {
                "scope": f"{app.id}:write",
                "app": app.id,
                "label": f"Add and change your {app.name} data",
                "personal": personal,
            }
        )
    out.append(
        {
            "scope": "hub:read",
            "app": "",
            "label": "See the Business hub (revenue, activity)",
            "personal": False,
        }
    )
    out.append(
        {
            "scope": "hub:write",
            "app": "",
            "label": "Change the business profile and send messages",
            "personal": False,
        }
    )
    return out


SCOPES = {entry["scope"]: entry for entry in scope_catalog()}


def parse_scopes(value: Any) -> list[str]:
    raw = (
        value if isinstance(value, list) else str(value or "").replace(",", " ").split()
    )
    out: list[str] = []
    for item in raw:
        scope = str(item).strip()
        if not scope:
            continue
        if scope not in SCOPES:
            raise ValidationFailed(f"Unknown scope: {scope}.", code="invalid_scope")
        if scope not in out:
            out.append(scope)
    return out


def required_scope(path: str, method: str) -> str | None:
    """The scope a request needs, or None when no token may call it at all."""
    if path.startswith("/api/suite/"):
        return "hub:read" if method == "GET" else "hub:write"
    if path == "/api/oauth/userinfo":
        return "profile"
    owner = app_catalog.app_of_path(path)
    if owner is None:
        return None
    return f"{owner.id}:read" if method == "GET" else f"{owner.id}:write"


def satisfied(held: list[str], needed: str) -> bool:
    """``<app>:write`` includes ``<app>:read``."""
    if needed in held:
        return True
    if needed.endswith(":read"):
        return needed[: -len(":read")] + ":write" in held
    return False


# ---------------------------------------------------------------------------
# redirect addresses
# ---------------------------------------------------------------------------


def clean_redirect(uri: str) -> str:
    text = str(uri or "").strip()
    parts = urlsplit(text)
    if parts.fragment or not parts.netloc:
        raise ValidationFailed("A redirect address is a full URL without a #fragment.")
    host = (parts.hostname or "").lower()
    loopback = host == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if parts.scheme == "https" or (parts.scheme == "http" and loopback):
        return text
    raise ValidationFailed(
        "Redirect addresses use https (or http on 127.0.0.1 / localhost for apps on a computer)."
    )


def _verify_pkce(verifier: str, challenge: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9\-._~]{43,128}", verifier or ""):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return crypto.equal(computed, challenge)


# ---------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------


class OAuth:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    @property
    def db(self):  # noqa: ANN201
        return self.app.db

    @property
    def now(self) -> int:
        return self.app.clock.now()

    # -- apps (clients) -------------------------------------------------------------------------

    def _public_client(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "client_id": row["client_id"],
            "name": row["name"],
            "homepage": row["homepage"],
            "redirect_uris": loads(row["redirect_uris"], []),
            "scopes": loads(row["scopes"], []),
            "grants": loads(row["grants"], []),
            "confidential": bool(row["confidential"]),
            "created_at": row["created_at"],
            "revoked_at": row["revoked_at"],
        }

    def clients(self, actor: Principal) -> list[dict[str, Any]]:
        self.app.authz.require(actor, "integrations.read")
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT * FROM oauth_clients WHERE org_id = ? AND revoked_at IS NULL ORDER BY created_at",
                (actor.org_id,),
            )
        return [self._public_client(row) for row in rows]

    def create_client(self, actor: Principal, raw: dict[str, Any]) -> dict[str, Any]:
        self.app.authz.require(actor, "integrations.manage")
        self.app.entitlements.require_state(actor.org_id, write=True)
        name = str(raw.get("name") or "").strip()
        if not 2 <= len(name) <= 80:
            raise ValidationFailed("Give the app a name (2 to 80 characters).")
        confidential = bool(raw.get("confidential", True))
        uris = [
            clean_redirect(u)
            for u in (raw.get("redirect_uris") or [])
            if str(u).strip()
        ]
        grants = [
            g
            for g in (raw.get("grants") or ["authorization_code", "refresh_token"])
            if g in GRANTS
        ]
        if "client_credentials" in grants and not confidential:
            raise ValidationFailed(
                "Only a confidential app (with a secret) can act for the organization."
            )
        if "authorization_code" in grants and not uris:
            raise ValidationFailed(
                "Add at least one redirect address for people to come back to."
            )
        scopes = parse_scopes(raw.get("scopes") or [])
        if not scopes:
            raise ValidationFailed("Choose what the app may do (at least one scope).")
        homepage = str(raw.get("homepage") or "").strip()
        if homepage and not homepage.startswith("https://"):
            raise ValidationFailed("The app's homepage starts with https://.")
        with self.db.read() as connection:
            count = one(
                connection,
                "SELECT COUNT(*) AS n FROM oauth_clients WHERE org_id = ? AND revoked_at IS NULL",
                (actor.org_id,),
            )
        if count and int(count["n"]) >= MAX_CLIENTS:
            raise Conflict(f"An organization can register at most {MAX_CLIENTS} apps.")
        client_id = "aira_app_" + crypto.new_token(18)
        secret = ("aira_secret_" + crypto.new_token(32)) if confidential else ""
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO oauth_clients (client_id, org_id, name, homepage, secret_hash, redirect_uris, scopes, grants, confidential, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    client_id,
                    actor.org_id,
                    name,
                    homepage,
                    crypto.hash_token(secret) if secret else "",
                    dumps(uris),
                    dumps(scopes),
                    dumps(grants),
                    1 if confidential else 0,
                    actor.actor_id,
                    self.now,
                ),
            )
            self.app.audit.record(
                actor.org_id,
                "oauth.client_create",
                actor=actor,
                target_type="oauth_client",
                target_id=client_id,
                detail={"name": name, "scopes": scopes, "grants": grants},
                connection=connection,
            )
        client = self.client(client_id)
        return {
            **self._public_client(client),
            "client_secret": secret,
        }  # the secret is shown once

    def client(self, client_id: str) -> dict[str, Any]:
        with platform_scope(), self.db.read() as connection:
            row = one(
                connection,
                "SELECT * FROM oauth_clients WHERE client_id = ? AND revoked_at IS NULL",
                (str(client_id),),
            )
        if row is None:
            raise NotFound("App not found.")
        return row

    def _own_client(self, actor: Principal, client_id: str) -> dict[str, Any]:
        row = self.client(client_id)
        if row["org_id"] != actor.org_id:
            raise NotFound("App not found.")
        return row

    def update_client(
        self, actor: Principal, client_id: str, raw: dict[str, Any]
    ) -> dict[str, Any]:
        self.app.authz.require(actor, "integrations.manage")
        row = self._own_client(actor, client_id)
        values: dict[str, Any] = {}
        if "name" in raw:
            name = str(raw["name"] or "").strip()
            if not 2 <= len(name) <= 80:
                raise ValidationFailed("Give the app a name (2 to 80 characters).")
            values["name"] = name
        if "redirect_uris" in raw:
            values["redirect_uris"] = dumps(
                [
                    clean_redirect(u)
                    for u in raw["redirect_uris"] or []
                    if str(u).strip()
                ]
            )
        if "scopes" in raw:
            scopes = parse_scopes(raw["scopes"])
            if not scopes:
                raise ValidationFailed("Choose at least one scope.")
            values["scopes"] = dumps(scopes)
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            with self.db.write() as connection:
                connection.execute(
                    f"UPDATE oauth_clients SET {sets} WHERE client_id = ?",
                    (*values.values(), row["client_id"]),
                )  # noqa: S608 - fixed column names
                self.app.audit.record(
                    actor.org_id,
                    "oauth.client_update",
                    actor=actor,
                    target_type="oauth_client",
                    target_id=client_id,
                    detail={"fields": sorted(values)},
                    connection=connection,
                )
        return self._public_client(self.client(client_id))

    def rotate_secret(self, actor: Principal, client_id: str) -> dict[str, Any]:
        self.app.authz.require(actor, "integrations.manage")
        row = self._own_client(actor, client_id)
        if not row["confidential"]:
            raise Conflict("A public app has no secret.")
        secret = "aira_secret_" + crypto.new_token(32)
        with self.db.write() as connection:
            connection.execute(
                "UPDATE oauth_clients SET secret_hash = ? WHERE client_id = ?",
                (crypto.hash_token(secret), client_id),
            )
            self.app.audit.record(
                actor.org_id,
                "oauth.client_secret_rotate",
                actor=actor,
                target_type="oauth_client",
                target_id=client_id,
                connection=connection,
            )
        return {"client_id": client_id, "client_secret": secret}

    def delete_client(self, actor: Principal, client_id: str) -> None:
        self.app.authz.require(actor, "integrations.manage")
        self._own_client(actor, client_id)
        with self.db.write() as connection:
            connection.execute(
                "UPDATE oauth_clients SET revoked_at = ? WHERE client_id = ?",
                (self.now, client_id),
            )
            connection.execute(
                "UPDATE oauth_tokens SET revoked_at = ? WHERE client_id = ? AND revoked_at IS NULL",
                (self.now, client_id),
            )
            self.app.audit.record(
                actor.org_id,
                "oauth.client_delete",
                actor=actor,
                target_type="oauth_client",
                target_id=client_id,
                connection=connection,
            )

    def authenticate_client(self, client_id: str, secret: str) -> dict[str, Any]:
        """The app making a token request (its secret for a confidential app)."""
        try:
            row = self.client(client_id)
        except NotFound:
            raise OAuthError("invalid_client", "Unknown app.", 401) from None
        if row["confidential"]:
            if not secret or not crypto.equal(
                crypto.hash_token(secret), row["secret_hash"]
            ):
                raise OAuthError("invalid_client", "The app's secret is wrong.", 401)
        elif secret:
            raise OAuthError("invalid_client", "A public app has no secret.", 401)
        if self.app.entitlements.resolve(row["org_id"]).read_only:
            raise OAuthError(
                "unauthorized_client",
                "The organization's subscription is not active.",
                403,
            )
        return row

    # -- authorization (consent) ------------------------------------------------------------

    def authorization_request(
        self, actor: Principal, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Check an /oauth/authorize request and describe it for the consent screen."""
        if params.get("response_type", "code") != "code":
            raise ValidationFailed(
                "Only response_type=code is supported.",
                code="unsupported_response_type",
            )
        client = self.client(str(params.get("client_id") or ""))
        redirect = str(params.get("redirect_uri") or "")
        if redirect not in loads(client["redirect_uris"], []):
            raise ValidationFailed(
                "That redirect address is not registered for the app.",
                code="invalid_redirect_uri",
            )
        if "authorization_code" not in loads(client["grants"], []):
            raise ValidationFailed(
                "This app cannot ask people for access.", code="unauthorized_client"
            )
        scopes = parse_scopes(params.get("scope") or "")
        allowed = set(loads(client["scopes"], []))
        if not scopes or not set(scopes) <= allowed:
            raise ValidationFailed(
                "The app asked for access it is not registered for.",
                code="invalid_scope",
            )
        challenge = str(params.get("code_challenge") or "")
        method = str(params.get("code_challenge_method") or "")
        if challenge or not client["confidential"]:
            if method != "S256" or not re.fullmatch(r"[A-Za-z0-9\-_]{43}", challenge):
                raise ValidationFailed(
                    "PKCE with S256 is required (code_challenge, code_challenge_method=S256).",
                    code="invalid_request",
                )
        if client["org_id"] != actor.org_id and not self._cross_org_allowed(client):
            raise Forbidden(
                "This app belongs to another organization.", code="access_denied"
            )
        org = self.app.tenancy.org(client["org_id"])
        return {
            "client": {
                "client_id": client["client_id"],
                "name": client["name"],
                "homepage": client["homepage"],
                "organization": org["name"],
            },
            "redirect_uri": redirect,
            "scopes": [SCOPES[s] for s in scopes],
            "state": str(params.get("state") or "")[:500],
            "code_challenge": challenge,
            "code_challenge_method": method,
        }

    def _cross_org_allowed(self, client: dict[str, Any]) -> bool:
        """Apps work for the organization that registered them (a marketplace would relax this)."""
        return False

    def decide(self, actor: Principal, params: dict[str, Any], allow: bool) -> str:
        """The person allowed or denied: the address to send the browser back to."""
        request = self.authorization_request(actor, params)
        redirect = request["redirect_uri"]
        sep = "&" if "?" in redirect else "?"
        state = {"state": request["state"]} if request["state"] else {}
        if not allow:
            return f"{redirect}{sep}{urlencode({'error': 'access_denied', **state})}"
        code = crypto.new_token(32)
        scopes = [s["scope"] for s in request["scopes"]]
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO oauth_codes (code_hash, org_id, client_id, member_id, redirect_uri, scopes, challenge, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    crypto.hash_token(code),
                    actor.org_id,
                    request["client"]["client_id"],
                    actor.member_id,
                    redirect,
                    dumps(scopes),
                    request["code_challenge"],
                    self.now,
                    self.now + CODE_TTL,
                ),
            )
            self.app.audit.record(
                actor.org_id,
                "oauth.consent",
                actor=actor,
                target_type="oauth_client",
                target_id=request["client"]["client_id"],
                detail={"scopes": scopes},
                connection=connection,
            )
        return f"{redirect}{sep}{urlencode({'code': code, **state})}"

    # -- tokens -----------------------------------------------------------------------------------

    def _issue(
        self,
        connection: Any,
        *,
        org_id: str,
        client_id: str,
        member_id: str,
        scopes: list[str],
        family: str = "",
        refresh: bool = True,
    ) -> dict[str, Any]:
        access = "aira_at_" + crypto.new_token(32)
        family = family or new_id()
        connection.execute(
            """INSERT INTO oauth_tokens (token_hash, kind, org_id, client_id, member_id, scopes, family_id, name, created_at, expires_at)
            VALUES (?, 'access', ?, ?, ?, ?, ?, '', ?, ?)""",
            (
                crypto.hash_token(access),
                org_id,
                client_id,
                member_id,
                dumps(scopes),
                family,
                self.now,
                self.now + ACCESS_TTL,
            ),
        )
        out: dict[str, Any] = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ACCESS_TTL,
            "scope": " ".join(scopes),
        }
        if refresh:
            refresh_token = "aira_rt_" + crypto.new_token(32)
            connection.execute(
                """INSERT INTO oauth_tokens (token_hash, kind, org_id, client_id, member_id, scopes, family_id, name, created_at, expires_at)
                VALUES (?, 'refresh', ?, ?, ?, ?, ?, '', ?, ?)""",
                (
                    crypto.hash_token(refresh_token),
                    org_id,
                    client_id,
                    member_id,
                    dumps(scopes),
                    family,
                    self.now,
                    self.now + REFRESH_TTL,
                ),
            )
            out["refresh_token"] = refresh_token
        return out

    def token(
        self, form: dict[str, Any], basic: tuple[str, str] | None
    ) -> dict[str, Any]:
        """The token endpoint (RFC 6749 section 4.1.3, 4.4, 6)."""
        client_id, secret = basic or (
            str(form.get("client_id") or ""),
            str(form.get("client_secret") or ""),
        )
        client = self.authenticate_client(client_id, secret)
        grant = str(form.get("grant_type") or "")
        grants = loads(client["grants"], [])
        if grant not in grants:
            raise OAuthError(
                "unauthorized_client", f"This app may not use {grant or 'that grant'}."
            )
        if grant == "authorization_code":
            return self._from_code(client, form)
        if grant == "refresh_token":
            return self._from_refresh(client, str(form.get("refresh_token") or ""))
        if grant == "client_credentials":
            wanted = parse_scopes(form.get("scope") or "") or loads(
                client["scopes"], []
            )
            if not set(wanted) <= set(loads(client["scopes"], [])):
                raise OAuthError(
                    "invalid_scope", "The app is not registered for that access."
                )
            personal = [
                s for s in wanted if s.split(":")[0] in PERSONAL_APPS or s == "profile"
            ]
            if personal:
                raise OAuthError(
                    "invalid_scope",
                    f"An app acting for the organization cannot have personal scopes: {', '.join(personal)}.",
                )
            with self.db.write() as connection:
                issued = self._issue(
                    connection,
                    org_id=client["org_id"],
                    client_id=client["client_id"],
                    member_id="",
                    scopes=wanted,
                    refresh=False,
                )
            return issued
        raise OAuthError(
            "unsupported_grant_type",
            "Use authorization_code, refresh_token or client_credentials.",
        )

    def _from_code(
        self, client: dict[str, Any], form: dict[str, Any]
    ) -> dict[str, Any]:
        code = str(form.get("code") or "")
        with self.db.read() as connection:
            used = one(
                connection,
                "SELECT family_id, used_at, client_id FROM oauth_codes WHERE code_hash = ?",
                (crypto.hash_token(code),),
            )
        if (
            used is not None
            and used["used_at"]
            and used["client_id"] == client["client_id"]
        ):
            # A code used twice: revoke what it produced (RFC 6749 section 4.1.2), committed before refusing.
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE oauth_tokens SET revoked_at = ? WHERE family_id = ? AND revoked_at IS NULL",
                    (self.now, used["family_id"] or ""),
                )
            raise OAuthError("invalid_grant", "The code was already used.")
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT * FROM oauth_codes WHERE code_hash = ?",
                (crypto.hash_token(code),),
            )
            if row is None or row["client_id"] != client["client_id"] or row["used_at"]:
                raise OAuthError("invalid_grant", "The code is not valid.")
            if row["expires_at"] <= self.now:
                raise OAuthError("invalid_grant", "The code has expired.")
            if str(form.get("redirect_uri") or "") != row["redirect_uri"]:
                raise OAuthError(
                    "invalid_grant",
                    "redirect_uri does not match the one in the authorization request.",
                )
            if row["challenge"]:
                if not _verify_pkce(
                    str(form.get("code_verifier") or ""), row["challenge"]
                ):
                    raise OAuthError(
                        "invalid_grant", "The PKCE code_verifier does not match."
                    )
            elif not client["confidential"]:
                raise OAuthError("invalid_grant", "PKCE is required for this app.")
            family = new_id()
            connection.execute(
                "UPDATE oauth_codes SET used_at = ?, family_id = ? WHERE code_hash = ?",
                (self.now, family, row["code_hash"]),
            )
            member = one(
                connection,
                "SELECT status, removed_at FROM members WHERE member_id = ? AND org_id = ?",
                (row["member_id"], row["org_id"]),
            )
            if member is None or member["removed_at"] or member["status"] != "active":
                raise OAuthError(
                    "invalid_grant",
                    "The person who allowed this is no longer a member.",
                )
            issued = self._issue(
                connection,
                org_id=row["org_id"],
                client_id=client["client_id"],
                member_id=row["member_id"],
                scopes=loads(row["scopes"], []),
                family=family,
            )
        return issued

    def _from_refresh(self, client: dict[str, Any], token: str) -> dict[str, Any]:
        with self.db.read() as connection:
            seen = one(
                connection,
                "SELECT family_id, client_id, revoked_at, rotated_at FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'",
                (crypto.hash_token(token),),
            )
        if (
            seen is not None
            and seen["client_id"] == client["client_id"]
            and (seen["revoked_at"] or seen["rotated_at"])
        ):
            # An old refresh token came back: someone may have stolen it. End the whole family (committed first).
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE oauth_tokens SET revoked_at = ? WHERE family_id = ? AND revoked_at IS NULL",
                    (self.now, seen["family_id"]),
                )
            raise OAuthError(
                "invalid_grant",
                "The refresh token was already used; every token from it is revoked.",
            )
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'",
                (crypto.hash_token(token),),
            )
            if (
                row is None
                or row["client_id"] != client["client_id"]
                or row["revoked_at"]
                or row["rotated_at"]
            ):
                raise OAuthError("invalid_grant", "The refresh token is not valid.")
            if row["expires_at"] <= self.now:
                raise OAuthError("invalid_grant", "The refresh token has expired.")
            connection.execute(
                "UPDATE oauth_tokens SET rotated_at = ? WHERE token_hash = ?",
                (self.now, row["token_hash"]),
            )
            return self._issue(
                connection,
                org_id=row["org_id"],
                client_id=client["client_id"],
                member_id=row["member_id"],
                scopes=loads(row["scopes"], []),
                family=row["family_id"],
            )

    def revoke(self, token: str, client: dict[str, Any] | None = None) -> None:
        """RFC 7009: revoking a refresh token ends its family; an unknown token is not an error."""
        with self.db.write() as connection:
            row = one(
                connection,
                "SELECT * FROM oauth_tokens WHERE token_hash = ?",
                (crypto.hash_token(token),),
            )
            if row is None or (
                client is not None and row["client_id"] != client["client_id"]
            ):
                return
            if row["kind"] == "refresh":
                connection.execute(
                    "UPDATE oauth_tokens SET revoked_at = ? WHERE family_id = ? AND revoked_at IS NULL",
                    (self.now, row["family_id"]),
                )
            else:
                connection.execute(
                    "UPDATE oauth_tokens SET revoked_at = ? WHERE token_hash = ?",
                    (self.now, row["token_hash"]),
                )

    def introspect(self, token: str, client: dict[str, Any]) -> dict[str, Any]:
        """RFC 7662, for the app's own tokens."""
        row = self._live(token)
        if row is None or row["client_id"] != client["client_id"]:
            return {"active": False}
        return {
            "active": True,
            "scope": " ".join(loads(row["scopes"], [])),
            "client_id": row["client_id"],
            "token_type": "Bearer" if row["kind"] != "refresh" else "refresh_token",
            "exp": row["expires_at"],
            "iat": row["created_at"],
            "sub": row["member_id"] or f"org:{row['org_id']}",
        }

    def _live(self, token: str) -> dict[str, Any] | None:
        if not token or not token.startswith(("aira_at_", "aira_rt_", "aira_pat_")):
            return None
        with platform_scope(), self.db.read() as connection:
            row = one(
                connection,
                "SELECT * FROM oauth_tokens WHERE token_hash = ?",
                (crypto.hash_token(token),),
            )
        if (
            row is None
            or row["revoked_at"]
            or row["rotated_at"]
            or (row["expires_at"] and row["expires_at"] <= self.now)
        ):
            return None
        return row

    # -- personal access tokens ----------------------------------------------------------------

    def personal_tokens(self, actor: Principal) -> list[dict[str, Any]]:
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT * FROM oauth_tokens WHERE org_id = ? AND member_id = ? AND kind = 'pat' AND revoked_at IS NULL ORDER BY created_at DESC",
                (actor.org_id, actor.member_id),
            )
        return [
            {
                "id": row["family_id"],
                "name": row["name"],
                "scopes": loads(row["scopes"], []),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "last_used_at": row["last_used_at"],
            }
            for row in rows
        ]

    def create_personal_token(
        self, actor: Principal, raw: dict[str, Any]
    ) -> dict[str, Any]:
        if actor.is_operator or not actor.member_id:
            raise Forbidden("Only members make personal access tokens.")
        self.app.entitlements.require_state(actor.org_id, write=True)
        name = str(raw.get("name") or "").strip()
        if not 2 <= len(name) <= 60:
            raise ValidationFailed(
                "Name the token (2 to 60 characters) so you know what uses it."
            )
        scopes = parse_scopes(raw.get("scopes") or [])
        if not scopes:
            raise ValidationFailed("Choose what the token may do.")
        days = int(raw.get("days") or 90)
        if days not in PAT_DAYS:
            raise ValidationFailed(
                f"Expiry must be one of {', '.join(str(d) for d in PAT_DAYS)} days."
            )
        if len(self.personal_tokens(actor)) >= MAX_PATS:
            raise Conflict(f"You can keep at most {MAX_PATS} tokens. Remove one first.")
        token = "aira_pat_" + crypto.new_token(32)
        family = new_id()
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO oauth_tokens (token_hash, kind, org_id, client_id, member_id, scopes, family_id, name, created_at, expires_at)
                VALUES (?, 'pat', ?, '', ?, ?, ?, ?, ?, ?)""",
                (
                    crypto.hash_token(token),
                    actor.org_id,
                    actor.member_id,
                    dumps(scopes),
                    family,
                    name,
                    self.now,
                    self.now + days * 86_400,
                ),
            )
            self.app.audit.record(
                actor.org_id,
                "oauth.pat_create",
                actor=actor,
                target_type="token",
                target_id=family,
                detail={"name": name, "scopes": scopes, "days": days},
                connection=connection,
            )
        return {
            "id": family,
            "name": name,
            "scopes": scopes,
            "token": token,
            "expires_at": self.now + days * 86_400,
        }

    def revoke_personal_token(self, actor: Principal, token_id: str) -> None:
        with self.db.write() as connection:
            changed = connection.execute(
                "UPDATE oauth_tokens SET revoked_at = ? WHERE family_id = ? AND org_id = ? AND member_id = ? AND kind = 'pat' AND revoked_at IS NULL",
                (self.now, token_id, actor.org_id, actor.member_id),
            ).rowcount
            if not changed:
                raise NotFound("Token not found.")
            self.app.audit.record(
                actor.org_id,
                "oauth.pat_revoke",
                actor=actor,
                target_type="token",
                target_id=token_id,
                connection=connection,
            )

    # -- connected apps (what a person allowed) -----------------------------------------------

    def connected(self, actor: Principal) -> list[dict[str, Any]]:
        with self.db.read() as connection:
            rows = many(
                connection,
                """SELECT t.client_id, c.name, MAX(t.created_at) AS since, t.scopes FROM oauth_tokens t
                JOIN oauth_clients c ON c.client_id = t.client_id
                WHERE t.org_id = ? AND t.member_id = ? AND t.kind = 'refresh' AND t.revoked_at IS NULL AND t.rotated_at IS NULL
                GROUP BY t.client_id, c.name, t.scopes""",
                (actor.org_id, actor.member_id),
            )
        return [
            {
                "client_id": r["client_id"],
                "name": r["name"],
                "since": r["since"],
                "scopes": loads(r["scopes"], []),
            }
            for r in rows
        ]

    def disconnect(self, actor: Principal, client_id: str) -> None:
        with self.db.write() as connection:
            ended = connection.execute(
                "UPDATE oauth_tokens SET revoked_at = ? WHERE org_id = ? AND member_id = ? AND client_id = ? AND revoked_at IS NULL",
                (self.now, actor.org_id, actor.member_id, client_id),
            ).rowcount
            if not ended:  # not an app this person connected (another organization's ids are the same 404)
                raise LookupError("That app is not connected to your account.")
            self.app.audit.record(
                actor.org_id,
                "oauth.disconnect",
                actor=actor,
                target_type="oauth_client",
                target_id=client_id,
                connection=connection,
            )

    # -- using a token -----------------------------------------------------------------------------

    def principal(self, token: str) -> Principal | None:
        """Who a bearer token acts as, with its scopes in ``extra["oauth"]`` (None: not a valid token)."""
        row = self._live(token)
        if row is None or row["kind"] == "refresh":
            return None
        scopes = loads(row["scopes"], [])
        org_id = row["org_id"]
        with platform_scope(), self.db.read() as connection:
            org = one(
                connection,
                "SELECT status, deleted_at FROM organizations WHERE org_id = ?",
                (org_id,),
            )
        if org is None or org["deleted_at"] or org["status"] != "active":
            return None
        oauth = {"scopes": scopes, "client_id": row["client_id"], "kind": row["kind"]}
        with self.db.write() as connection:
            connection.execute(
                "UPDATE oauth_tokens SET last_used_at = ? WHERE token_hash = ?",
                (self.now, row["token_hash"]),
            )
        if row["member_id"]:
            with platform_scope(), self.db.read() as connection:
                member = one(
                    connection,
                    "SELECT * FROM members WHERE member_id = ? AND org_id = ? AND removed_at IS NULL AND status = 'active'",
                    (row["member_id"], org_id),
                )
            if member is None:
                return None
            base = Principal(
                kind="member",
                org_id=org_id,
                member_id=member["member_id"],
                role=member["role"],
                email=member["email"],
                name=member["name"],
                mfa_verified=True,
                mfa_enabled=bool(member.get("mfa_enabled_at")),
                auth_method="oauth",
                extra={"email_verified": True},
            )
        else:
            client = self.client(row["client_id"])
            base = Principal(
                kind="member",
                org_id=org_id,
                member_id="",
                role=ADMIN,
                email="",
                name=f"app:{client['name']}",
                mfa_verified=True,
                mfa_enabled=True,  # an app proves itself with its secret, not a second factor
                auth_method="oauth_client",
                extra={"email_verified": True, "service": True},
            )
        return replace(base, extra={**base.extra, "oauth": oauth})

    def require_scope(self, principal: Principal, path: str, method: str) -> None:
        oauth = principal.extra.get("oauth")
        if not oauth:
            return
        needed = required_scope(path, method)
        if needed is None:
            raise Forbidden(
                "This route cannot be called with an access token.",
                code="token_not_allowed",
            )
        if not satisfied(oauth["scopes"], needed):
            raise Forbidden(
                f"The token needs the {needed} scope.",
                code="insufficient_scope",
                scope=needed,
            )
        if principal.extra.get("service") and needed.split(":")[0] in PERSONAL_APPS:
            raise Forbidden(
                "An app acting for the organization cannot reach personal data.",
                code="insufficient_scope",
            )

    def userinfo(self, principal: Principal) -> dict[str, Any]:
        if principal.extra.get("service"):
            raise Forbidden(
                "An app acting for the organization has no person.",
                code="insufficient_scope",
            )
        org = self.app.tenancy.org(principal.org_id)
        return {
            "sub": principal.member_id,
            "name": principal.name,
            "email": principal.email,
            "email_verified": True,
            "organization": {"id": principal.org_id, "name": org["name"]},
            "role": principal.role,
        }

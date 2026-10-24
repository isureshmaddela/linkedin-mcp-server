"""Single sign-on and SCIM HTTP routes."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, quote

from aira_cloud.errors import (
    CloudError,
    TooManyRequests,
    Unauthorized,
    ValidationFailed,
)
from aira_cloud.routes.auth import session_cookie
from aira_cloud.sso import SCIM_ERROR
from aira_cloud.webio import Reply, Req, cloud_route

SCIM_TYPE = "application/scim+json"


def _form(req: Req) -> dict[str, str]:
    parsed = parse_qs(
        req.request.body.decode("utf-8", "replace"), keep_blank_values=True
    )
    return {key: values[0] for key, values in parsed.items()}


def _limit(req: Req) -> None:
    key = f"sso:{req.request.client_ip}"
    limiter = req.app.gateway.auth_limiter
    allowed, retry = limiter.check(key)
    if not allowed:
        raise TooManyRequests(
            f"Too many attempts. Try again in {retry // 60 + 1} minutes.",
            retry_after=retry,
        )
    limiter.hit(key)


def _redirect(location: str, cookies: list[str] | None = None) -> Reply:
    return Reply(
        302,
        b"",
        headers=[("Location", location)],
        content_type="text/plain",
        cookies=cookies or [],
    )


def _signin_error(error: CloudError) -> Reply:
    """A browser is mid-navigation: send it back to the sign-in screen with the reason, not a JSON page."""
    return _redirect(
        f"/#/signin?sso_error={quote(error.message)}&sso_code={quote(error.code)}"
    )


# ----------------------------------------------------------------------------- administration


@cloud_route("GET", r"/api/org/sso")
def sso_get(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "sso.manage")
    payload = req.app.sso.public_config(req.principal.org_id)
    entitlements = req.app.entitlements.resolve(req.principal.org_id)
    payload["available"] = entitlements.has("auth.sso")
    payload["scim_available"] = entitlements.has("auth.scim")
    return 200, payload


@cloud_route("POST", r"/api/org/sso", writes=True)
def sso_configure(req: Req):  # noqa: ANN201
    body = req.body()
    protocol = str(body.get("protocol", "")).lower()
    sso = req.app.sso
    common = {
        "domains": body.get("domains", []),
        "default_role": str(body.get("default_role", "candidate")),
        "group_role_map": body.get("group_role_map"),
        "enforced": bool(body.get("enforced", False)),
    }
    if protocol == "oidc":
        return 200, sso.configure_oidc(
            req.principal,
            issuer=str(body.get("issuer", "")),
            client_id=str(body.get("client_id", "")),
            client_secret=str(body.get("client_secret", "")),
            groups_claim=str(body.get("groups_claim", "groups")),
            **common,
        )
    if protocol == "saml":
        return 200, sso.configure_saml(
            req.principal,
            idp_entity_id=str(body.get("idp_entity_id", "")),
            sso_url=str(body.get("sso_url", "")),
            idp_certificate_pem=str(body.get("idp_certificate", "")),
            attribute_map=body.get("attribute_map"),
            **common,
        )
    raise ValidationFailed("Choose a protocol: oidc or saml.")


@cloud_route("DELETE", r"/api/org/sso", writes=True)
def sso_remove(req: Req):  # noqa: ANN201
    req.app.sso.remove_config(req.principal)
    return 200, {"ok": True}


@cloud_route("GET", r"/api/org/sso/scim-tokens")
def scim_tokens(req: Req):  # noqa: ANN201
    return 200, {
        "tokens": req.app.sso.list_scim_tokens(req.principal),
        "base_url": req.app.sso.sp_details(req.principal.org_id)["scim_base_url"],
    }


@cloud_route("POST", r"/api/org/sso/scim-tokens", writes=True)
def scim_token_create(req: Req):  # noqa: ANN201
    return 201, req.app.sso.create_scim_token(
        req.principal, str(req.body().get("label", ""))
    )


@cloud_route("DELETE", r"/api/org/sso/scim-tokens/([0-9a-f]{16})", writes=True)
def scim_token_revoke(req: Req):  # noqa: ANN201
    req.app.sso.revoke_scim_token(req.principal, req.group())
    return 200, {"ok": True}


# ----------------------------------------------------------------------------- sign-in


@cloud_route("POST", r"/api/sso/discover", auth="public")
def sso_discover(req: Req):  # noqa: ANN201
    _limit(req)
    return 200, req.app.sso.discover(str(req.body().get("email", "")))


@cloud_route("GET", r"/api/sso/([a-z0-9-]{1,60})/start", auth="public")
def sso_start(req: Req):  # noqa: ANN201
    _limit(req)
    slug = req.group()
    try:
        config = req.app.sso.get_config(req.app.sso._org_by_slug(slug)["org_id"])
        if not config or config["protocol"] not in {"oidc", "saml"}:
            raise CloudError(
                "Single sign-on is not set up for this organization.", code="not_found"
            )
        url = (
            req.app.sso.oidc_start(slug)
            if config["protocol"] == "oidc"
            else req.app.sso.saml_start(slug)
        )
    except CloudError as error:
        return _signin_error(error)
    return _redirect(url)


@cloud_route("GET", r"/api/sso/oidc/callback", auth="public")
def oidc_callback(req: Req):  # noqa: ANN201
    _limit(req)
    if req.q("error"):
        return _signin_error(
            Unauthorized(
                f"The identity provider refused the sign-in: {req.q('error')[:80]}.",
                code="sso_denied",
            )
        )
    try:
        result = req.app.sso.oidc_callback(
            req.q("code"),
            req.q("state"),
            ip=req.request.client_ip,
            user_agent=req.request.headers.get("user-agent", ""),
        )
    except CloudError as error:
        return _signin_error(error)
    return _redirect("/#/", [session_cookie(req, result["token"])])


@cloud_route("GET", r"/api/org/sso/saml-metadata")
def saml_metadata_for_admins(req: Req):  # noqa: ANN201
    """SP metadata for the identity provider's administrator, available before SAML is configured."""
    req.app.authz.require(req.principal, "sso.manage")
    slug = req.app.tenancy.org(req.principal.org_id)["slug"]
    return Reply(
        200,
        req.app.sso.saml_metadata(slug, public=False).encode("utf-8"),
        content_type="application/samlmetadata+xml",
        filename="aira-sp-metadata.xml",
    )


@cloud_route("GET", r"/api/sso/([a-z0-9-]{1,60})/saml/metadata", auth="public")
def saml_metadata(req: Req):  # noqa: ANN201
    return Reply(
        200,
        req.app.sso.saml_metadata(req.group()).encode("utf-8"),
        content_type="application/samlmetadata+xml",
    )


@cloud_route("POST", r"/api/sso/([a-z0-9-]{1,60})/saml/acs", auth="public", csrf=False)
def saml_acs(req: Req):  # noqa: ANN201
    _limit(req)
    form = _form(req)
    try:
        result = req.app.sso.saml_acs(
            req.group(),
            form.get("SAMLResponse", ""),
            form.get("RelayState", ""),
            ip=req.request.client_ip,
            user_agent=req.request.headers.get("user-agent", ""),
        )
    except CloudError as error:
        return _signin_error(error)
    return _redirect("/#/", [session_cookie(req, result["token"])])


# ----------------------------------------------------------------------------- SCIM 2.0


def _scim_error(status: int, detail: str, scim_type: str = "") -> Reply:
    body: dict[str, Any] = {
        "schemas": [SCIM_ERROR],
        "status": str(status),
        "detail": detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    return Reply(status, body, content_type=SCIM_TYPE)


def _scim(handler: Callable[..., Any]) -> Callable[[Req], Reply]:
    """Authenticate the bearer token, then answer with SCIM envelopes (including for errors)."""

    def wrapped(req: Req) -> Reply:
        try:
            limiter = req.app.gateway.auth_limiter
            allowed, _retry = limiter.check(f"scim:{req.request.client_ip}")
            if not allowed:
                return _scim_error(429, "Too many rejected tokens.")
            try:
                org_id = req.app.sso.scim_authenticate(
                    req.request.headers.get("authorization", "")
                )
            except CloudError as error:
                limiter.hit(f"scim:{req.request.client_ip}")
                return _scim_error(401, error.message)
            result = handler(req, org_id)
            if isinstance(result, Reply):
                return result
            status, body = result
            return Reply(status, body, content_type=SCIM_TYPE)
        except CloudError as error:
            status = {
                "conflict": 409,
                "not_found": 404,
                "invalid": 400,
                "plan_required": 403,
                "forbidden": 403,
            }.get(error.code, error.status)
            scim_type = str(error.extra.get("scim_type", "")) or (
                "uniqueness" if error.code == "conflict" else ""
            )
            return _scim_error(status, error.message, scim_type)

    wrapped.__name__ = getattr(handler, "__name__", "scim_handler")
    return wrapped


def _scim_route(method: str, pattern: str):  # noqa: ANN202
    def decorator(handler: Callable[..., Any]):  # noqa: ANN202
        return cloud_route(method, pattern, auth="public", csrf=False)(_scim(handler))

    return decorator


_ID = r"([0-9a-f]{32})"


@_scim_route("GET", r"/scim/v2/ServiceProviderConfig")
def scim_service_provider_config(req: Req, org_id: str):  # noqa: ANN201
    return 200, {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "Bearer token",
                "description": "A SCIM token created by an organization owner or admin.",
            }
        ],
    }


@_scim_route("GET", r"/scim/v2/Users")
def scim_users_list(req: Req, org_id: str):  # noqa: ANN201
    return 200, req.app.sso.scim_list_users(
        org_id,
        filter_text=req.q("filter"),
        start=req.qint("startIndex", 1, 1, 10_000_000),
        count=req.qint("count", 100, 0, 200),
    )


@_scim_route("POST", r"/scim/v2/Users")
def scim_users_create(req: Req, org_id: str):  # noqa: ANN201
    return 201, req.app.sso.scim_create_user(org_id, req.body())


@_scim_route("GET", rf"/scim/v2/Users/{_ID}")
def scim_user_get(req: Req, org_id: str):  # noqa: ANN201
    return 200, req.app.sso.scim_user(
        org_id, req.app.sso._scim_member(org_id, req.group())
    )


@_scim_route("PUT", rf"/scim/v2/Users/{_ID}")
def scim_user_put(req: Req, org_id: str):  # noqa: ANN201
    return 200, req.app.sso.scim_replace_user(org_id, req.group(), req.body())


@_scim_route("PATCH", rf"/scim/v2/Users/{_ID}")
def scim_user_patch(req: Req, org_id: str):  # noqa: ANN201
    return 200, req.app.sso.scim_patch_user(org_id, req.group(), req.body())


@_scim_route("DELETE", rf"/scim/v2/Users/{_ID}")
def scim_user_delete(req: Req, org_id: str):  # noqa: ANN201
    req.app.sso.scim_deactivate(org_id, req.group())
    return Reply(204, b"", content_type=SCIM_TYPE)


@_scim_route("GET", r"/scim/v2/Groups")
def scim_groups_list(req: Req, org_id: str):  # noqa: ANN201
    return 200, req.app.sso.scim_list_groups(org_id)


@_scim_route("POST", r"/scim/v2/Groups")
def scim_groups_create(req: Req, org_id: str):  # noqa: ANN201
    return 201, req.app.sso.scim_create_group(org_id, req.body())


@_scim_route("GET", rf"/scim/v2/Groups/{_ID}")
def scim_group_get(req: Req, org_id: str):  # noqa: ANN201
    return 200, req.app.sso.scim_get_group(org_id, req.group())


@_scim_route("PATCH", rf"/scim/v2/Groups/{_ID}")
def scim_group_patch(req: Req, org_id: str):  # noqa: ANN201
    return 200, req.app.sso.scim_patch_group(org_id, req.group(), req.body())


@_scim_route("DELETE", rf"/scim/v2/Groups/{_ID}")
def scim_group_delete(req: Req, org_id: str):  # noqa: ANN201
    req.app.sso.scim_delete_group(org_id, req.group())
    return Reply(204, b"", content_type=SCIM_TYPE)


__all__ = ["re"]

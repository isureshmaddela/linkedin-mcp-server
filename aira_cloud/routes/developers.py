"""The developer platform's routes: OAuth 2.0 (RFC 6749, 7636, 7009, 7662, 8414), apps, tokens and webhooks.

Protocol endpoints (no session; an app authenticates itself):
  GET  /.well-known/oauth-authorization-server   server metadata
  GET  /oauth/authorize                           sends the person to the consent screen (#/oauth?...)
  POST /oauth/token                               codes, refresh tokens and client credentials -> tokens
  POST /oauth/revoke                              RFC 7009
  POST /oauth/introspect                          RFC 7662 (the app's own tokens)
For people (session):
  GET/POST /api/oauth/authorize                   describe the request / allow or deny it
  GET      /api/oauth/userinfo                    with a token that has the "profile" scope
  /api/developers/...                             apps, personal access tokens, connected apps, webhooks, scopes,
                                                  and the OpenAPI description of what a token can reach
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import parse_qs, urlencode

from aira_cloud.oauth import SCOPES, OAuthError, required_scope, scope_catalog
from aira_cloud.webhooks import event_names
from aira_cloud.webio import Reply, Req, cloud_route

TOKEN_HEADERS = [("Cache-Control", "no-store"), ("Pragma", "no-cache")]


def _oauth_reply(error: OAuthError) -> Reply:
    headers = list(TOKEN_HEADERS)
    if error.status == 401:
        headers.append(("WWW-Authenticate", 'Basic realm="aira"'))
    return Reply(error.status, error.payload(), headers=headers)


def _basic(req: Req) -> tuple[str, str] | None:
    header = req.request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise OAuthError(
            "invalid_client", "The Authorization header is not valid.", 401
        ) from None
    client_id, _, secret = decoded.partition(":")
    from urllib.parse import unquote

    return unquote(client_id), unquote(secret)


def _params(req: Req) -> dict[str, Any]:
    return {name: values[0] for name, values in req.query.items() if values}


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------


@cloud_route("GET", r"/\.well-known/oauth-authorization-server", auth="public")
def metadata(req: Req):  # noqa: ANN201
    base = req.app.config.base_url.rstrip("/")
    return 200, {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "introspection_endpoint": f"{base}/oauth/introspect",
        "userinfo_endpoint": f"{base}/api/oauth/userinfo",
        "scopes_supported": sorted(SCOPES),
        "response_types_supported": ["code"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            "client_credentials",
        ],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
            "none",
        ],
        "service_documentation": f"{base}/#/developers",
    }


@cloud_route("GET", r"/oauth/authorize", auth="public")
def authorize_redirect(req: Req):  # noqa: ANN201
    """The browser lands on the app's consent screen with the request as it came."""
    query = urlencode(_params(req))
    return Reply(
        302,
        {"redirect": f"/#/oauth?{query}"},
        headers=[("Location", f"/#/oauth?{query}")],
    )


@cloud_route("POST", r"/oauth/token", auth="public", csrf=False)
def token(req: Req):  # noqa: ANN201
    form = req.body() or {
        k: v[0]
        for k, v in parse_qs(req.request.body.decode("utf-8", "replace")).items()
    }
    try:
        issued = req.app.oauth.token(form, _basic(req))
    except OAuthError as error:
        return _oauth_reply(error)
    return Reply(200, issued, headers=list(TOKEN_HEADERS))


@cloud_route("POST", r"/oauth/revoke", auth="public", csrf=False)
def revoke(req: Req):  # noqa: ANN201
    form = req.body()
    try:
        basic = _basic(req)
        client = req.app.oauth.authenticate_client(
            *(
                basic
                or (
                    str(form.get("client_id") or ""),
                    str(form.get("client_secret") or ""),
                )
            )
        )
    except OAuthError as error:
        return _oauth_reply(error)
    req.app.oauth.revoke(str(form.get("token") or ""), client)
    return Reply(200, {}, headers=list(TOKEN_HEADERS))


@cloud_route("POST", r"/oauth/introspect", auth="public", csrf=False)
def introspect(req: Req):  # noqa: ANN201
    form = req.body()
    try:
        basic = _basic(req)
        client = req.app.oauth.authenticate_client(
            *(
                basic
                or (
                    str(form.get("client_id") or ""),
                    str(form.get("client_secret") or ""),
                )
            )
        )
    except OAuthError as error:
        return _oauth_reply(error)
    if not client["confidential"]:
        return _oauth_reply(
            OAuthError(
                "unauthorized_client",
                "Only a confidential app may introspect tokens.",
                401,
            )
        )
    return Reply(
        200,
        req.app.oauth.introspect(str(form.get("token") or ""), client),
        headers=list(TOKEN_HEADERS),
    )


# ---------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------


@cloud_route("GET", r"/api/oauth/authorize")
def authorize_info(req: Req):  # noqa: ANN201
    return 200, req.app.oauth.authorization_request(req.principal, _params(req))


@cloud_route("POST", r"/api/oauth/authorize", writes=True)
def authorize_decide(req: Req):  # noqa: ANN201
    body = req.body()
    params = {k: str(v) for k, v in (body.get("request") or {}).items()}
    return 200, {
        "redirect": req.app.oauth.decide(req.principal, params, bool(body.get("allow")))
    }


@cloud_route("GET", r"/api/oauth/userinfo")
def userinfo(req: Req):  # noqa: ANN201
    return 200, req.app.oauth.userinfo(req.principal)


# ---------------------------------------------------------------------------
# apps, tokens, connections
# ---------------------------------------------------------------------------

ID = r"([A-Za-z0-9_-]{8,80})"


@cloud_route("GET", r"/api/developers/overview")
def overview(req: Req):  # noqa: ANN201
    principal = req.principal
    can_manage = req.app.authz.can(principal, "integrations.manage")
    return 200, {
        "base_url": req.app.config.base_url.rstrip("/"),
        "can_manage": can_manage,
        "can_read": req.app.authz.can(principal, "integrations.read"),
        "scopes": scope_catalog(),
        "events": event_names(),
        "personal_tokens": req.app.oauth.personal_tokens(principal),
        "connected": req.app.oauth.connected(principal),
    }


@cloud_route("GET", r"/api/developers/clients")
def clients(req: Req):  # noqa: ANN201
    return 200, {"clients": req.app.oauth.clients(req.principal)}


@cloud_route("POST", r"/api/developers/clients", writes=True)
def clients_create(req: Req):  # noqa: ANN201
    return 201, {"client": req.app.oauth.create_client(req.principal, req.body())}


@cloud_route("PATCH", rf"/api/developers/clients/{ID}", writes=True)
def clients_update(req: Req):  # noqa: ANN201
    return 200, {
        "client": req.app.oauth.update_client(req.principal, req.group(), req.body())
    }


@cloud_route("POST", rf"/api/developers/clients/{ID}/secret", writes=True)
def clients_secret(req: Req):  # noqa: ANN201
    return 200, req.app.oauth.rotate_secret(req.principal, req.group())


@cloud_route("DELETE", rf"/api/developers/clients/{ID}", writes=True)
def clients_delete(req: Req):  # noqa: ANN201
    req.app.oauth.delete_client(req.principal, req.group())
    return 200, {"deleted": True}


@cloud_route("POST", r"/api/developers/tokens", writes=True)
def tokens_create(req: Req):  # noqa: ANN201
    return 201, {
        "token": req.app.oauth.create_personal_token(req.principal, req.body())
    }


@cloud_route("DELETE", rf"/api/developers/tokens/{ID}", writes=True)
def tokens_delete(req: Req):  # noqa: ANN201
    req.app.oauth.revoke_personal_token(req.principal, req.group())
    return 200, {"revoked": True}


@cloud_route("DELETE", rf"/api/developers/connected/{ID}", writes=True)
def connected_delete(req: Req):  # noqa: ANN201
    req.app.oauth.disconnect(req.principal, req.group())
    return 200, {"disconnected": True}


# ---------------------------------------------------------------------------
# webhooks
# ---------------------------------------------------------------------------


@cloud_route("GET", r"/api/developers/webhooks")
def webhooks_list(req: Req):  # noqa: ANN201
    return 200, {
        "endpoints": req.app.webhooks.endpoints(req.principal),
        "events": event_names(),
    }


@cloud_route("POST", r"/api/developers/webhooks", writes=True)
def webhooks_create(req: Req):  # noqa: ANN201
    return 201, {"endpoint": req.app.webhooks.create(req.principal, req.body())}


@cloud_route("PATCH", rf"/api/developers/webhooks/{ID}", writes=True)
def webhooks_update(req: Req):  # noqa: ANN201
    return 200, {
        "endpoint": req.app.webhooks.update(req.principal, req.group(), req.body())
    }


@cloud_route("DELETE", rf"/api/developers/webhooks/{ID}", writes=True)
def webhooks_delete(req: Req):  # noqa: ANN201
    req.app.webhooks.delete(req.principal, req.group())
    return 200, {"deleted": True}


@cloud_route("POST", rf"/api/developers/webhooks/{ID}/secret", writes=True)
def webhooks_secret(req: Req):  # noqa: ANN201
    return 200, req.app.webhooks.rotate_secret(req.principal, req.group())


@cloud_route("POST", rf"/api/developers/webhooks/{ID}/test", writes=True)
def webhooks_test(req: Req):  # noqa: ANN201
    return 200, req.app.webhooks.send_test(req.principal, req.group())


@cloud_route("GET", rf"/api/developers/webhooks/{ID}/deliveries")
def webhooks_deliveries(req: Req):  # noqa: ANN201
    return 200, {"deliveries": req.app.webhooks.deliveries(req.principal, req.group())}


# ---------------------------------------------------------------------------
# what a token can reach (OpenAPI 3.1)
# ---------------------------------------------------------------------------


@cloud_route("GET", r"/api/developers/openapi\.json")
def openapi(req: Req):  # noqa: ANN201
    from aira.airaapi import http as airaapi_http

    airaapi_http.load_route_modules()
    base = req.app.config.base_url.rstrip("/")
    paths: dict[str, dict[str, Any]] = {}
    for entry in airaapi_http.AIRA_ROUTES:
        if entry.auth != "session":
            continue
        pattern = entry.pattern.pattern
        path = _template(pattern)
        if path is None:
            continue
        method = entry.method.lower()
        scope = required_scope(path, entry.method)
        if scope is None:
            continue
        doc = (entry.handler.__doc__ or "").strip().split("\n")[0]
        paths.setdefault(path, {})[method] = {
            "operationId": entry.attr,
            "summary": doc
            or str(getattr(entry.handler, "__name__", entry.attr))
            .replace("_", " ")
            .capitalize(),
            "security": [{"oauth2": [scope]}, {"bearer": []}],
            "x-scope": scope,
            "responses": {
                "200": {"description": "OK"},
                "401": {"description": "No or invalid token"},
                "403": {"description": "Missing scope"},
            },
        }
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "Aira API",
            "version": "1",
            "description": "Every app's API. Use an OAuth access token or a personal access token as a Bearer token.",
        },
        "servers": [{"url": base}],
        "components": {
            "securitySchemes": {
                "oauth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": f"{base}/oauth/authorize",
                            "tokenUrl": f"{base}/oauth/token",
                            "refreshUrl": f"{base}/oauth/token",
                            "scopes": {k: v["label"] for k, v in SCOPES.items()},
                        },
                        "clientCredentials": {
                            "tokenUrl": f"{base}/oauth/token",
                            "scopes": {
                                k: v["label"]
                                for k, v in SCOPES.items()
                                if not v["personal"]
                            },
                        },
                    },
                },
                "bearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "A personal access token (aira_pat_...)",
                },
            }
        },
        "paths": dict(sorted(paths.items())),
    }
    return 200, spec


def _template(pattern: str) -> str | None:
    """A route regex as an OpenAPI path ("/api/shop/products/([0-9a-f]{32})" -> "/api/shop/products/{id}")."""
    import re

    count = 0

    def hole(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "{id}" if count == 1 else f"{{id{count}}}"

    path = re.sub(r"\([^()]*\)", hole, pattern).replace("\\.", ".")
    if any(ch in path for ch in "()[]?*+\\^$|"):
        return None
    return path


# ---------------------------------------------------------------------------
# hosted rules for the Developers app's newer routes
# ---------------------------------------------------------------------------


# The gateway refuses a local route that aira_cloud.policy.POLICY does not list (default deny). The rank tracker,
# the Earnings tab and editing a key's terms came after that table, so their rules are registered here, with the
# same permissions and plan features as the console routes beside them, until policy.py lists them itself (an
# entry there wins: ``setdefault``). Reads need ``integrations.read`` (owner, admin, auditor), changes
# ``integrations.manage`` (owner, admin); every route sits under /api/airaapi/, so it answers 402 app_required
# until the organization subscribes to the Developers app.
def developers_rules() -> dict[str, Any]:
    from aira_cloud.policy import Rule

    def read(**kw: Any) -> Rule:
        return Rule("org_api", "integrations.read", feature="api.console_read", **kw)

    def manage(feature: str, **kw: Any) -> Rule:
        return Rule("org_api", "integrations.manage", write=True, feature=feature, **kw)

    return {
        "airaapi__routes__rank_list": read(),
        "airaapi__routes__rank_detail": read(),
        "airaapi__routes__rank_report": read(audit=True),
        "airaapi__routes__rank_create": manage("api.searches"),
        "airaapi__routes__rank_update": manage("api.searches"),
        "airaapi__routes__rank_delete": manage("api.searches", audit=True),
        "airaapi__routes__rank_run": manage("api.searches", kind="api_search"),
        "airaapi__routes__earnings_report": read(),
        "airaapi__routes__earnings_statement": read(audit=True),
        "airaapi__routes__earnings_settings": manage("api.console_read", audit=True),
        "airaapi__routes__keys_update": manage("api.v1", audit=True),
    }


def _register_developers_rules() -> None:
    from aira_cloud.policy import POLICY

    for name, rule in developers_rules().items():
        POLICY.setdefault(name, rule)


_register_developers_rules()

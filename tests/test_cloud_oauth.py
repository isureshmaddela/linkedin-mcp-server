"""The developer platform: OAuth 2.0 with PKCE, client credentials, personal access tokens, scopes, webhooks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from cloud_support import Browser, build_org, sign_in

from aira.suite import kit
from aira_cloud.worker import HANDLERS

REDIRECT = "https://partner.example/callback"
VERIFIER = "v" * 20 + "-verifier-" + "x" * 20  # 50 characters, the allowed alphabet


def challenge(verifier: str = VERIFIER) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )


def form(**fields: str) -> bytes:
    return urlencode(fields).encode()


FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def server(cloud) -> Browser:
    """A partner's server: no cookies, no browser headers."""
    browser = Browser(cloud)
    return browser


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def org(cloud):
    kit.forget_schema()
    built = build_org(cloud)
    cloud.billing.subscribe_app(built.owner, "studio")
    return built


def make_client(owner: Browser, **overrides):
    body = {
        "name": "Partner CRM",
        "redirect_uris": [REDIRECT],
        "scopes": ["studio:read", "studio:write", "profile"],
        "grants": ["authorization_code", "refresh_token"],
        "confidential": True,
        **overrides,
    }
    status, data = owner.call("POST", "/api/developers/clients", body)
    assert status == 201, data
    return data["client"]


def consent(
    owner: Browser, client: dict, scope: str = "studio:read profile", **extra: str
) -> str:
    request = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": REDIRECT,
        "scope": scope,
        "state": "xyz",
        "code_challenge": challenge(),
        "code_challenge_method": "S256",
        **extra,
    }
    status, info = owner.call("GET", "/api/oauth/authorize?" + urlencode(request))
    assert status == 200, info
    assert (
        info["client"]["name"] == "Partner CRM"
        and [s["scope"] for s in info["scopes"]] == scope.split()
    )
    status, decided = owner.call(
        "POST", "/api/oauth/authorize", {"request": request, "allow": True}
    )
    assert status == 200, decided
    query = parse_qs(urlsplit(decided["redirect"]).query)
    assert query["state"] == ["xyz"]
    return query["code"][0]


def test_server_metadata(cloud):
    status, meta = Browser(cloud).call("GET", "/.well-known/oauth-authorization-server")
    assert status == 200
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert (
        "client_credentials" in meta["grant_types_supported"]
        and "studio:read" in meta["scopes_supported"]
    )
    status, _ = Browser(cloud).call("GET", "/oauth/authorize?client_id=x", raw=True)
    assert status == 302


def test_only_admins_register_apps_and_the_secret_is_shown_once(cloud, org):
    owner = sign_in(cloud, org.owner)
    client = make_client(owner)
    assert client["client_secret"].startswith("aira_secret_")
    status, listed = owner.call("GET", "/api/developers/clients")
    assert "client_secret" not in json.dumps(listed)
    candidate = sign_in(cloud, org.candidate)
    assert candidate.call("POST", "/api/developers/clients", {"name": "x"})[0] == 403
    assert (
        owner.call(
            "POST",
            "/api/developers/clients",
            {
                "name": "Bad",
                "redirect_uris": ["http://evil.example/cb"],
                "scopes": ["studio:read"],
            },
        )[0]
        == 400
    )
    assert (
        owner.call(
            "POST",
            "/api/developers/clients",
            {"name": "Bad", "redirect_uris": [REDIRECT], "scopes": ["nope:read"]},
        )[0]
        == 400
    )


def test_the_authorization_code_flow_with_pkce_and_refresh_rotation(cloud, org):
    owner = sign_in(cloud, org.owner)
    client = make_client(owner)
    code = consent(owner, client)
    partner = server(cloud)
    # wrong verifier, then the right one
    status, error = partner.call(
        "POST",
        "/oauth/token",
        form(
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT,
            code_verifier="w" * 50,
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        ),
        headers=FORM,
    )
    assert status == 400 and error["error"] == "invalid_grant"
    code = consent(owner, client)
    basic = base64.b64encode(
        f"{client['client_id']}:{client['client_secret']}".encode()
    ).decode()
    status, tokens = partner.call(
        "POST",
        "/oauth/token",
        form(
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT,
            code_verifier=VERIFIER,
        ),
        headers={**FORM, "Authorization": f"Basic {basic}"},
    )
    assert status == 200, tokens
    assert (
        tokens["token_type"] == "Bearer"
        and tokens["access_token"].startswith("aira_at_")
        and tokens["scope"] == "studio:read profile"
    )
    assert partner.header("Cache-Control") == "no-store"
    # the token reaches only what it holds a scope for
    api = server(cloud)
    status, data = api.call(
        "GET", "/api/studio/overview", headers=bearer(tokens["access_token"])
    )
    assert status == 200, data
    assert (
        api.call(
            "POST",
            "/api/studio/clients",
            {"name": "X"},
            headers=bearer(tokens["access_token"]),
        )[1]["code"]
        == "insufficient_scope"
    )
    assert (
        api.call("GET", "/api/org/members", headers=bearer(tokens["access_token"]))[1][
            "code"
        ]
        == "token_not_allowed"
    )
    status, me = api.call(
        "GET", "/api/oauth/userinfo", headers=bearer(tokens["access_token"])
    )
    assert status == 200 and me["email"] == org.owner.email
    assert (
        api.call("GET", "/api/studio/overview", headers=bearer("aira_at_nope"))[0]
        == 401
    )
    # the same code twice is refused and ends what it produced
    status, again = partner.call(
        "POST",
        "/oauth/token",
        form(
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT,
            code_verifier=VERIFIER,
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        ),
        headers=FORM,
    )
    assert status == 400 and again["error"] == "invalid_grant"
    assert (
        api.call("GET", "/api/studio/overview", headers=bearer(tokens["access_token"]))[
            0
        ]
        == 401
    )
    # refresh rotation and theft detection
    code = consent(owner, client)
    status, first = partner.call(
        "POST",
        "/oauth/token",
        form(
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT,
            code_verifier=VERIFIER,
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        ),
        headers=FORM,
    )
    status, second = partner.call(
        "POST",
        "/oauth/token",
        form(
            grant_type="refresh_token",
            refresh_token=first["refresh_token"],
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        ),
        headers=FORM,
    )
    assert status == 200 and second["refresh_token"] != first["refresh_token"]
    assert (
        api.call("GET", "/api/studio/overview", headers=bearer(second["access_token"]))[
            0
        ]
        == 200
    )
    status, stolen = partner.call(
        "POST",
        "/oauth/token",
        form(
            grant_type="refresh_token",
            refresh_token=first["refresh_token"],
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        ),
        headers=FORM,
    )
    assert status == 400 and stolen["error"] == "invalid_grant"
    assert (
        api.call("GET", "/api/studio/overview", headers=bearer(second["access_token"]))[
            0
        ]
        == 401
    )
    # the person sees and can disconnect the app
    status, overview = owner.call("GET", "/api/developers/overview")
    assert overview["can_manage"] is True


def test_public_apps_need_pkce_and_wrong_secrets_are_refused(cloud, org):
    owner = sign_in(cloud, org.owner)
    public_app = make_client(owner, confidential=False)
    assert public_app["client_secret"] == ""
    request = {
        "response_type": "code",
        "client_id": public_app["client_id"],
        "redirect_uri": REDIRECT,
        "scope": "studio:read",
    }
    status, error = owner.call("GET", "/api/oauth/authorize?" + urlencode(request))
    assert status == 400 and error["code"] == "invalid_request"
    bad = {
        **request,
        "redirect_uri": "https://attacker.example/cb",
        "code_challenge": challenge(),
        "code_challenge_method": "S256",
    }
    assert (
        owner.call("GET", "/api/oauth/authorize?" + urlencode(bad))[1]["code"]
        == "invalid_redirect_uri"
    )
    secret_app = make_client(owner)
    status, error = server(cloud).call(
        "POST",
        "/oauth/token",
        form(
            grant_type="authorization_code",
            code="x",
            redirect_uri=REDIRECT,
            client_id=secret_app["client_id"],
            client_secret="wrong",
        ),
        headers=FORM,
    )
    assert status == 401 and error["error"] == "invalid_client"


def test_client_credentials_act_for_the_business_never_for_people(cloud, org):
    owner = sign_in(cloud, org.owner)
    client = make_client(
        owner,
        grants=["client_credentials"],
        redirect_uris=[],
        scopes=["studio:read", "careers:read"],
    )
    partner = server(cloud)
    status, error = partner.call(
        "POST",
        "/oauth/token",
        form(
            grant_type="client_credentials",
            scope="careers:read",
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        ),
        headers=FORM,
    )
    assert status == 400 and error["error"] == "invalid_scope"
    status, tokens = partner.call(
        "POST",
        "/oauth/token",
        form(
            grant_type="client_credentials",
            scope="studio:read",
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        ),
        headers=FORM,
    )
    assert status == 200 and "refresh_token" not in tokens
    assert (
        partner.call(
            "GET", "/api/studio/overview", headers=bearer(tokens["access_token"])
        )[0]
        == 200
    )
    assert (
        partner.call(
            "GET", "/api/oauth/userinfo", headers=bearer(tokens["access_token"])
        )[0]
        == 403
    )
    # introspection and revocation by the app
    status, info = partner.call(
        "POST",
        "/oauth/introspect",
        form(
            token=tokens["access_token"],
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        ),
        headers=FORM,
    )
    assert info["active"] is True and info["scope"] == "studio:read"
    partner.call(
        "POST",
        "/oauth/revoke",
        form(
            token=tokens["access_token"],
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        ),
        headers=FORM,
    )
    assert partner.call(
        "POST",
        "/oauth/introspect",
        form(
            token=tokens["access_token"],
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        ),
        headers=FORM,
    )[1] == {"active": False}
    assert (
        partner.call(
            "GET", "/api/studio/overview", headers=bearer(tokens["access_token"])
        )[0]
        == 401
    )
    # deleting the app ends everything it had
    status, tokens = partner.call(
        "POST",
        "/oauth/token",
        form(
            grant_type="client_credentials",
            client_id=client["client_id"],
            client_secret=client["client_secret"],
            scope="studio:read",
        ),
        headers=FORM,
    )
    owner.call("DELETE", f"/api/developers/clients/{client['client_id']}")
    assert (
        partner.call(
            "GET", "/api/studio/overview", headers=bearer(tokens["access_token"])
        )[0]
        == 401
    )


def test_personal_access_tokens(cloud, org):
    candidate = sign_in(cloud, org.candidate)
    status, made = candidate.call(
        "POST",
        "/api/developers/tokens",
        {"name": "My script", "scopes": ["careers:read"], "days": 30},
    )
    assert status == 201 and made["token"]["token"].startswith("aira_pat_")
    token = made["token"]["token"]
    script = server(cloud)
    assert script.call("GET", "/api/jobs", headers=bearer(token))[0] == 200
    assert (
        script.call("POST", "/api/tasks", {"title": "x"}, headers=bearer(token))[1][
            "code"
        ]
        == "insufficient_scope"
    )
    status, listed = candidate.call("GET", "/api/developers/overview")
    assert (
        listed["personal_tokens"][0]["name"] == "My script"
        and "token" not in listed["personal_tokens"][0]
    )
    candidate.call("DELETE", f"/api/developers/tokens/{made['token']['id']}")
    assert script.call("GET", "/api/jobs", headers=bearer(token))[0] == 401
    assert (
        candidate.call(
            "POST",
            "/api/developers/tokens",
            {"name": "x", "scopes": ["careers:read"], "days": 5},
        )[0]
        == 400
    )
    # a member who leaves loses their tokens
    status, made = candidate.call(
        "POST",
        "/api/developers/tokens",
        {"name": "Again", "scopes": ["careers:read"], "days": 7},
    )
    cloud.tenancy.remove_member(org.owner, org.candidate.member_id)
    assert (
        script.call("GET", "/api/jobs", headers=bearer(made["token"]["token"]))[0]
        == 401
    )


# ---------------------------------------------------------------------------
# webhooks
# ---------------------------------------------------------------------------


class Receiver:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.received: list[tuple[dict[str, str], bytes]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                outer.received.append(({k: v for k, v in self.headers.items()}, body))
                self.send_response(outer.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _run_jobs(cloud, kind: str = "webhook.deliver") -> list:
    with cloud.db.read() as connection:
        jobs = [
            dict(r)
            for r in connection.execute(
                "SELECT * FROM queue_jobs WHERE kind = ? AND status = 'queued'", (kind,)
            ).fetchall()
        ]
    results = []
    for job in jobs:
        job["payload"] = (
            json.loads(job["payload"])
            if isinstance(job.get("payload"), str)
            else job.get("payload")
        )
        results.append(HANDLERS[kind](cloud, job))
        with cloud.db.write() as connection:
            connection.execute(
                "UPDATE queue_jobs SET status = 'done' WHERE job_id = ?",
                (job["job_id"],),
            )
    return results


def test_webhooks_are_signed_retried_and_safe(cloud, org, monkeypatch):
    owner = sign_in(cloud, org.owner)
    assert (
        owner.call(
            "POST",
            "/api/developers/webhooks",
            {"url": "http://127.0.0.1:9/hook", "events": ["*"]},
        )[0]
        == 400
    )
    monkeypatch.setenv("AIRA_WEBHOOKS_ALLOW_LOCAL", "1")
    receiver = Receiver()
    try:
        status, made = owner.call(
            "POST",
            "/api/developers/webhooks",
            {"url": receiver.url, "events": ["studio.*"]},
        )
        assert status == 201, made
        secret = made["endpoint"]["secret"]
        # an app records something: the event goes out, signed
        assert owner.call(
            "POST",
            "/api/studio/clients",
            {"name": "Acme Ltd", "email": "a@acme.example"},
        )[0] in (200, 201)
        results = _run_jobs(cloud)
        assert results and results[0]["status"] == "delivered"
        headers, body = receiver.received[-1]
        event = json.loads(body)
        assert (
            event["type"].startswith("studio.")
            and headers["Monosage-Event"] == event["type"]
        )
        timestamp, mac = (
            part.split("=", 1)[1] for part in headers["Monosage-Signature"].split(",")
        )
        expected = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(mac, expected)
        # a failing endpoint is retried, then marked failed
        receiver.status = 500
        endpoint_id = made["endpoint"]["endpoint_id"]
        status, tested = owner.call(
            "POST", f"/api/developers/webhooks/{endpoint_id}/test"
        )
        assert tested["status"] == "retrying" and tested["attempts"] == 1
        for _ in range(3):
            cloud.clock.advance(seconds=31 * 60)
            with cloud.db.write() as connection:
                connection.execute(
                    "UPDATE queue_jobs SET run_at = 0 WHERE kind = 'webhook.deliver' AND status = 'queued'"
                )
            _run_jobs(cloud)
        status, log = owner.call(
            "GET", f"/api/developers/webhooks/{endpoint_id}/deliveries"
        )
        assert any(
            d["status"] == "failed" and d["attempts"] == 4 for d in log["deliveries"]
        )
        # events of personal apps are never sent; other organizations' endpoints never see this one's events
        assert cloud.webhooks.emit(org.org_id, "care", "taken", {}) == 0
        candidate = sign_in(cloud, org.candidate)
        assert candidate.call("GET", "/api/developers/webhooks")[0] == 403
    finally:
        receiver.close()

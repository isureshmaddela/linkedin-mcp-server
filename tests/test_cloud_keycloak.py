"""OIDC and SAML sign-in against a real identity provider: Keycloak, driven by a real browser.

The in-repo IdP stub (tests/idp_stub.py) proves the protocol logic; this proves interoperability with a widely used
implementation: its discovery document, its token endpoint (PKCE, client secret), its group claims, its SAML
signatures and attribute statements. Skipped when Docker or a browser is not available.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from cloud_support import build_org, make_app, sign_in

from aira_cloud.gateway import Gateway, make_server

IMAGE = "quay.io/keycloak/keycloak:26.0"
PASSWORD = "kc-user-password-1"
OIDC_SECRET = "oidc-client-secret-" + "x" * 20


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=30
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _docker_ready(), reason="Docker is not available")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _realm(app_base: str, slug: str) -> dict:
    acs = f"{app_base}/api/sso/{slug}/saml/acs"
    entity = f"{app_base}/api/sso/{slug}/saml/metadata"

    def user(name: str, first: str, last: str, groups: list[str]) -> dict:
        return {
            "username": f"{name}@kc.test",
            "email": f"{name}@kc.test",
            "emailVerified": True,
            "enabled": True,
            "firstName": first,
            "lastName": last,
            "groups": groups,
            "credentials": [
                {"type": "password", "value": PASSWORD, "temporary": False}
            ],
        }

    return {
        "realm": "aira",
        "enabled": True,
        "sslRequired": "none",
        "groups": [{"name": "aira-admins"}, {"name": "aira-coaches"}],
        "users": [
            user("alice", "Alice", "Admin", ["/aira-admins"]),
            user("carol", "Carol", "Coach", ["/aira-coaches"]),
            user("newbie", "Nina", "Newcomer", []),
            user("dave", "Dave", "Dev", []),
        ],
        "clients": [
            {
                "clientId": "aira-oidc",
                "protocol": "openid-connect",
                "enabled": True,
                "publicClient": False,
                "secret": OIDC_SECRET,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "redirectUris": [f"{app_base}/api/sso/oidc/callback"],
                "attributes": {"pkce.code.challenge.method": "S256"},
                "protocolMappers": [
                    {
                        "name": "groups",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-group-membership-mapper",
                        "config": {
                            "claim.name": "groups",
                            "full.path": "false",
                            "id.token.claim": "true",
                            "access.token.claim": "true",
                            "userinfo.token.claim": "true",
                        },
                    }
                ],
            },
            {
                "clientId": entity,
                "protocol": "saml",
                "enabled": True,
                "redirectUris": [acs],
                "attributes": {
                    "saml.assertion.signature": "true",
                    "saml.server.signature": "false",
                    "saml.client.signature": "false",
                    "saml_name_id_format": "email",
                    "saml_assertion_consumer_url_post": acs,
                    "saml.force.post.binding": "true",
                    "saml.authnstatement": "true",
                },
                "protocolMappers": [
                    {
                        "name": "mail",
                        "protocol": "saml",
                        "protocolMapper": "saml-user-property-mapper",
                        "config": {
                            "user.attribute": "email",
                            "attribute.name": "mail",
                            "attribute.nameformat": "Basic",
                        },
                    },
                    {
                        "name": "display",
                        "protocol": "saml",
                        "protocolMapper": "saml-user-property-mapper",
                        "config": {
                            "user.attribute": "firstName",
                            "attribute.name": "displayName",
                            "attribute.nameformat": "Basic",
                        },
                    },
                    {
                        "name": "memberOf",
                        "protocol": "saml",
                        "protocolMapper": "saml-group-membership-mapper",
                        "config": {
                            "attribute.name": "memberOf",
                            "full.path": "false",
                            "single": "false",
                            "attribute.nameformat": "Basic",
                        },
                    },
                ],
            },
        ],
    }


class Stack:
    """The application's gateway on a real socket plus Keycloak in Docker."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.app_port = _free_port()
        self.kc_port = _free_port()
        self.app_base = f"http://127.0.0.1:{self.app_port}"
        self.kc_base = f"http://127.0.0.1:{self.kc_port}"
        self.container = f"aira-deploy-keycloak-{uuid.uuid4().hex[:8]}"
        self.server = None

    def start(self) -> None:
        self.app = make_app(self.tmp_path, base_url=self.app_base)
        # The test app runs on a fake clock fixed in the past; a real identity provider stamps real time.
        self.app.clock.now = lambda: int(time.time())  # type: ignore[method-assign]
        self.org = build_org(
            self.app, "Keycloak Test Org", plan="team", seats=25, slug_email="kc.test"
        )
        self.slug = self.app.tenancy.org(self.org.org_id)["slug"]
        realm_dir = self.tmp_path / "realm"
        realm_dir.mkdir()
        (realm_dir / "aira-realm.json").write_text(
            json.dumps(_realm(self.app_base, self.slug)), encoding="utf-8"
        )
        subprocess.run(["docker", "rm", "-f", self.container], capture_output=True)
        result = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.container,
                "-p",
                f"127.0.0.1:{self.kc_port}:8080",
                "-e",
                "KC_BOOTSTRAP_ADMIN_USERNAME=admin",
                "-e",
                "KC_BOOTSTRAP_ADMIN_PASSWORD=admin-password-1",
                "-e",
                f"KC_HOSTNAME={self.kc_base}",
                "-e",
                "KC_HTTP_ENABLED=true",
                "-v",
                f"{realm_dir}:/opt/keycloak/data/import:ro",
                IMAGE,
                "start-dev",
                "--import-realm",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            pytest.skip(f"Keycloak could not be started: {result.stderr[-300:]}")
        gateway = Gateway(self.app)
        self.server = make_server(gateway, "127.0.0.1", self.app_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        deadline = time.time() + 240
        while time.time() < deadline:
            try:
                urllib.request.urlopen(
                    f"{self.kc_base}/realms/aira/.well-known/openid-configuration",
                    timeout=3,
                )
                return
            except (urllib.error.URLError, OSError):
                time.sleep(2)
        logs = subprocess.run(
            ["docker", "logs", "--tail", "30", self.container],
            capture_output=True,
            text=True,
        ).stdout
        raise AssertionError("Keycloak never became ready:\n" + logs)

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        subprocess.run(["docker", "rm", "-f", self.container], capture_output=True)
        self.app.close()

    def idp_descriptor(self) -> dict[str, str]:
        raw = urllib.request.urlopen(
            f"{self.kc_base}/realms/aira/protocol/saml/descriptor", timeout=10
        ).read()
        root = ET.fromstring(raw)
        ns = {
            "md": "urn:oasis:names:tc:SAML:2.0:metadata",
            "ds": "http://www.w3.org/2000/09/xmldsig#",
        }
        certificate = root.find(".//ds:X509Certificate", ns).text.strip()  # type: ignore[union-attr]
        sso = next(
            e.attrib["Location"]
            for e in root.findall(".//md:SingleSignOnService", ns)
            if e.attrib["Binding"].endswith("HTTP-Redirect")
        )
        pem = (
            "-----BEGIN CERTIFICATE-----\n"
            + "\n".join(certificate[i : i + 64] for i in range(0, len(certificate), 64))
            + "\n-----END CERTIFICATE-----"
        )
        return {
            "entity_id": root.attrib["entityID"],
            "sso_url": sso,
            "certificate": pem,
        }


@pytest.fixture(scope="module")
def stack(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv(
        "AIRA_CLOUD_ALLOW_LOCAL_URLS", "1"
    )  # Keycloak and the app listen on loopback http
    try:
        from patchright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        pytest.skip("a browser driver is not installed")
    if (
        subprocess.run(
            ["docker", "image", "inspect", IMAGE], capture_output=True
        ).returncode
        != 0
        and subprocess.run(
            ["docker", "pull", IMAGE], capture_output=True, timeout=900
        ).returncode
        != 0
    ):
        pytest.skip("the Keycloak image could not be pulled")
    current = Stack(tmp_path_factory.mktemp("keycloak"))
    try:
        current.start()
        yield current
    finally:
        current.stop()


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    yield patch
    patch.undo()


@pytest.fixture(scope="module")
def browser():
    from patchright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


def _sign_in_through_keycloak(browser, stack: Stack, username: str) -> dict:
    """Follow the real redirect chain: the app, Keycloak's login form, and back."""
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto(f"{stack.app_base}/api/sso/{stack.slug}/start", wait_until="load")
        page.wait_for_selector("input[name=username]", timeout=60000)
        page.fill("input[name=username]", f"{username}@kc.test")
        page.fill("input[name=password]", PASSWORD)
        with page.expect_navigation(wait_until="load", timeout=60000):
            page.click("#kc-login")
        page.wait_for_url(re.compile(re.escape(stack.app_base)), timeout=60000)
        page.wait_for_load_state("load")
        response = context.request.get(f"{stack.app_base}/api/auth/me")
        return {
            "status": response.status,
            "me": response.json() if response.ok else {},
            "url": page.url,
        }
    finally:
        context.close()


def _admin(stack: Stack):
    return sign_in(stack.app, stack.org.owner)


def test_oidc_sign_in_through_keycloak_provisions_members_and_maps_groups(
    stack, browser
):
    owner = stack.org.owner
    stack.app.sso.configure_oidc(
        owner,
        issuer=f"{stack.kc_base}/realms/aira",
        client_id="aira-oidc",
        client_secret=OIDC_SECRET,
        domains=["kc.test"],
        default_role="candidate",
        group_role_map={"aira-admins": "admin", "aira-coaches": "coach"},
        groups_claim="groups",
    )

    alice = _sign_in_through_keycloak(browser, stack, "alice")
    assert alice["status"] == 200, alice
    assert alice["me"]["email"] == "alice@kc.test" and alice["me"]["role"] == "admin"

    carol = _sign_in_through_keycloak(browser, stack, "carol")
    assert carol["me"]["role"] == "coach"

    newbie = _sign_in_through_keycloak(browser, stack, "newbie")
    assert (
        newbie["me"]["role"] == "candidate"
        and newbie["me"]["email"] == "newbie@kc.test"
    )

    # a second sign-in links to the same member rather than creating another
    again = _sign_in_through_keycloak(browser, stack, "alice")
    assert again["me"]["member_id"] == alice["me"]["member_id"]
    members = [
        m
        for m in stack.app.tenancy.list_members(owner)
        if m["email"].endswith("@kc.test")
    ]
    assert len({m["email"] for m in members}) == len(members)


def test_oidc_with_a_wrong_client_secret_is_refused_by_keycloak(stack, browser):
    from aira_cloud.errors import ValidationFailed

    owner = stack.org.owner
    stack.app.sso.configure_oidc(
        owner,
        issuer=f"{stack.kc_base}/realms/aira",
        client_id="aira-oidc",
        client_secret="not-the-secret",
        domains=["kc.test"],
    )
    result = _sign_in_through_keycloak(browser, stack, "alice")
    assert result["status"] in (401, 403) or "sso_error" in result["url"], result
    assert ValidationFailed  # the configuration is accepted; the failure surfaces at sign-in, as an error page


def test_saml_sign_in_through_keycloak_with_a_mapped_attribute_set(stack, browser):
    owner = stack.org.owner
    idp = stack.idp_descriptor()
    stack.app.sso.configure_saml(
        owner,
        idp_entity_id=idp["entity_id"],
        sso_url=idp["sso_url"],
        idp_certificate_pem=idp["certificate"],
        domains=["kc.test"],
        default_role="candidate",
        group_role_map={"aira-admins": "admin", "aira-coaches": "coach"},
        attribute_map={"email": "mail", "name": "displayName", "groups": "memberOf"},
    )
    alice = _sign_in_through_keycloak(browser, stack, "alice")
    assert alice["status"] == 200, alice
    assert alice["me"]["email"] == "alice@kc.test" and alice["me"]["role"] in {
        "admin",
        "owner",
    }
    carol = _sign_in_through_keycloak(browser, stack, "carol")
    assert carol["me"]["role"] == "coach", carol
    # first seen through SAML: the name comes from the mapped displayName attribute, the email from `mail`
    dave = _sign_in_through_keycloak(browser, stack, "dave")
    assert dave["me"]["email"] == "dave@kc.test" and dave["me"]["name"] == "Dave", dave
    assert dave["me"]["role"] == "candidate"

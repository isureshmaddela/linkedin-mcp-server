"""Stand-in identity providers for the SSO tests: an OIDC provider on loopback and a SAML IdP that signs with signxml."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import threading
import uuid
import zlib
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"


def make_identity(common_name: str = "idp.test") -> tuple[bytes, str]:
    """A fresh RSA key and a self-signed certificate: ``(private key PEM, certificate PEM)``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return key_pem, certificate.public_bytes(serialization.Encoding.PEM).decode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# ----------------------------------------------------------------------------------- OIDC


class OidcProvider:
    """Discovery, JWKS and a token endpoint that checks the client secret, redirect URI and PKCE verifier."""

    def __init__(
        self,
        clock: Callable[[], int],
        *,
        client_id: str = "aira-client",
        client_secret: str = "s3cret-value",
    ) -> None:
        self.clock = clock
        self.client_id, self.client_secret = client_id, client_secret
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.codes: dict[str, dict[str, Any]] = {}
        self.token_requests: list[dict[str, str]] = []
        self.tamper: dict[str, Any] = {}
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def _send(self, status: int, body: dict[str, Any]) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/.well-known/openid-configuration":
                    self._send(200, provider.discovery())
                elif self.path == "/jwks":
                    self._send(200, provider.jwks())
                else:
                    self._send(404, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/token":
                    return self._send(404, {"error": "not_found"})
                length = int(self.headers.get("Content-Length", "0"))
                form = {
                    k: v[0]
                    for k, v in parse_qs(self.rfile.read(length).decode()).items()
                }
                status, body = provider.token(form)
                self._send(status, body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.issuer = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def discovery(self) -> dict[str, Any]:
        issuer = self.tamper.get("discovery_issuer", self.issuer)
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
        }

    def jwks(self) -> dict[str, Any]:
        numbers = self.key.public_key().public_numbers()
        encode = lambda n: _b64url(n.to_bytes((n.bit_length() + 7) // 8, "big"))  # noqa: E731
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": self.kid,
                    "n": encode(numbers.n),
                    "e": encode(numbers.e),
                }
            ]
        }

    def authorize(
        self,
        authorization_url: str,
        *,
        email: str,
        sub: str = "user-1",
        name: str = "",
        groups: list[str] | None = None,
        email_verified: bool = True,
    ) -> tuple[str, str]:
        """What the provider does after the person signs in: remember the request and hand back ``(code, state)``."""
        query = {
            k: v[0] for k, v in parse_qs(urlsplit(authorization_url).query).items()
        }
        assert (
            query["client_id"] == self.client_id
            and query["response_type"] == "code"
            and query["code_challenge_method"] == "S256"
        )
        code = uuid.uuid4().hex
        claims = {
            "sub": sub,
            "email": email,
            "email_verified": email_verified,
            "name": name,
        }
        if groups is not None:
            claims["groups"] = groups
        self.codes[code] = {
            "challenge": query["code_challenge"],
            "redirect_uri": query["redirect_uri"],
            "nonce": query["nonce"],
            "claims": claims,
        }
        return code, query["state"]

    def token(self, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        self.token_requests.append(form)
        entry = self.codes.pop(form.get("code", ""), None)
        if (
            entry is None
            or form.get("client_secret") != self.client_secret
            or form.get("client_id") != self.client_id
        ):
            return 400, {"error": "invalid_grant"}
        if form.get("redirect_uri") != entry["redirect_uri"]:
            return 400, {"error": "invalid_grant", "error_description": "redirect_uri"}
        if (
            _b64url(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
            != entry["challenge"]
        ):
            return 400, {
                "error": "invalid_grant",
                "error_description": "PKCE verifier does not match",
            }
        now = self.clock()
        claims = {
            "iss": self.issuer,
            "aud": self.tamper.get("aud", self.client_id),
            "iat": now,
            "exp": now + self.tamper.get("exp_offset", 300),
            "nonce": self.tamper.get("nonce", entry["nonce"]),
            **entry["claims"],
        }
        key = self.other_key if self.tamper.get("sign_with_other_key") else self.key
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        token = jwt.encode(claims, pem, algorithm="RS256", headers={"kid": self.kid})
        return 200, {"access_token": "at", "token_type": "Bearer", "id_token": token}


# ----------------------------------------------------------------------------------- SAML


class SamlIdp:
    """Builds SAML responses and signs them the way an identity provider would (RSA-SHA256, exclusive c14n)."""

    def __init__(
        self,
        clock: Callable[[], int],
        *,
        entity_id: str = "https://idp.test/entity",
        sso_url: str = "http://127.0.0.1:9/sso",
    ) -> None:
        self.clock = clock
        self.entity_id, self.sso_url = entity_id, sso_url
        self.key_pem, self.cert_pem = make_identity("idp.test")
        self.other_key_pem, self.other_cert_pem = make_identity("attacker.test")

    @staticmethod
    def parse_request(redirect_url: str) -> tuple[str, str]:
        """``(request id, relay state)`` from the redirect the service provider sent (inflating the AuthnRequest)."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(redirect_url).query).items()}
        xml = zlib.decompress(base64.b64decode(query["SAMLRequest"]), -15)
        root = etree.fromstring(xml)
        assert root.tag == f"{{{SAMLP_NS}}}AuthnRequest"
        return root.get("ID"), query["RelayState"]

    def _iso(self, offset: int) -> str:
        return dt.datetime.fromtimestamp(
            self.clock() + offset, dt.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    def assertion(
        self,
        *,
        request_id: str,
        acs: str,
        audience: str,
        email: str,
        name: str = "",
        groups: list[str] | None = None,
        issuer: str | None = None,
        recipient: str | None = None,
        not_before: int = -60,
        not_on_or_after: int = 300,
        assertion_id: str | None = None,
        name_id: str | None = None,
    ) -> etree._Element:
        attributes = f'<saml:Attribute Name="email"><saml:AttributeValue>{email}</saml:AttributeValue></saml:Attribute>'
        if name:
            attributes += f'<saml:Attribute Name="displayName"><saml:AttributeValue>{name}</saml:AttributeValue></saml:Attribute>'
        if groups is not None:
            attributes += (
                '<saml:Attribute Name="groups">'
                + "".join(
                    f"<saml:AttributeValue>{g}</saml:AttributeValue>" for g in groups
                )
                + "</saml:Attribute>"
            )
        xml = (
            f'<saml:Assertion xmlns:saml="{SAML_NS}" ID="{assertion_id or "_a" + uuid.uuid4().hex}" Version="2.0" IssueInstant="{self._iso(0)}">'
            f"<saml:Issuer>{issuer or self.entity_id}</saml:Issuer>"
            f'<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{name_id or email}</saml:NameID>'
            f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"><saml:SubjectConfirmationData InResponseTo="{request_id}" '
            f'Recipient="{recipient or acs}" NotOnOrAfter="{self._iso(not_on_or_after)}"/></saml:SubjectConfirmation></saml:Subject>'
            f'<saml:Conditions NotBefore="{self._iso(not_before)}" NotOnOrAfter="{self._iso(not_on_or_after)}"><saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction></saml:Conditions>'
            f'<saml:AuthnStatement AuthnInstant="{self._iso(0)}"><saml:AuthnContext><saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>'
            f"<saml:AttributeStatement>{attributes}</saml:AttributeStatement></saml:Assertion>"
        )
        return etree.fromstring(xml)

    def _sign(
        self,
        element: etree._Element,
        *,
        key_pem: bytes | None = None,
        cert_pem: str | None = None,
    ) -> etree._Element:
        from signxml import XMLSigner, methods

        signer = XMLSigner(
            method=methods.enveloped,
            signature_algorithm="rsa-sha256",
            digest_algorithm="sha256",
            c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
        )
        signed = signer.sign(
            element,
            key=key_pem or self.key_pem,
            cert=cert_pem or self.cert_pem,
            reference_uri="#" + element.get("ID"),
            id_attribute="ID",
        )
        signature = signed.find(f"{{{DS_NS}}}Signature")
        issuer = signed.find(f"{{{SAML_NS}}}Issuer")
        signed.remove(signature)
        issuer.addnext(
            signature
        )  # the schema wants the signature right after the issuer
        return signed

    def response(
        self,
        *,
        request_id: str,
        acs: str,
        audience: str,
        email: str,
        sign: str = "assertion",
        key: str = "idp",
        destination: str | None = None,
        response_in_response_to: str | None = None,
        status: str = "urn:oasis:names:tc:SAML:2.0:status:Success",
        evil: dict[str, Any] | None = None,
        graft_signature: bool = False,
        **assertion_options: Any,
    ) -> str:
        """A base64 SAML response. ``sign`` is assertion, response or none; ``evil`` adds an unsigned assertion first."""
        assertion = self.assertion(
            request_id=request_id,
            acs=acs,
            audience=audience,
            email=email,
            **assertion_options,
        )
        key_pem, cert_pem = (
            (self.key_pem, self.cert_pem)
            if key == "idp"
            else (self.other_key_pem, self.other_cert_pem)
        )
        if sign == "assertion":
            assertion = self._sign(assertion, key_pem=key_pem, cert_pem=cert_pem)
        response = etree.fromstring(
            f'<samlp:Response xmlns:samlp="{SAMLP_NS}" xmlns:saml="{SAML_NS}" ID="_r{uuid.uuid4().hex}" Version="2.0" IssueInstant="{self._iso(0)}" '
            f'Destination="{destination or acs}" InResponseTo="{response_in_response_to or request_id}">'
            f"<saml:Issuer>{self.entity_id}</saml:Issuer>"
            f'<samlp:Status><samlp:StatusCode Value="{status}"/></samlp:Status></samlp:Response>'
        )
        response.append(assertion)
        if sign == "response":
            response = self._sign(response, key_pem=key_pem, cert_pem=cert_pem)
        if evil is not None:
            attacker = self.assertion(
                request_id=request_id, acs=acs, audience=audience, **evil
            )
            if graft_signature:  # copy the real signature into the forged assertion: its reference still points at the real one
                real = assertion.find(f"{{{DS_NS}}}Signature")
                import copy

                attacker.find(f"{{{SAML_NS}}}Issuer").addnext(copy.deepcopy(real))
            response.insert(2, attacker)  # before the real assertion
        return base64.b64encode(etree.tostring(response)).decode()

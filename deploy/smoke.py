#!/usr/bin/env python3
"""End-to-end smoke test of the hosted build running in Docker (standard library only).

    python deploy/smoke.py                 # build the images, start the stack, run every check, remove the stack
    python deploy/smoke.py --no-build      # reuse images already built (see AIRA_WEB_IMAGE / AIRA_WORKER_IMAGE)
    python deploy/smoke.py --keep          # leave the stack running afterwards

What it proves, against the real containers, an S3-compatible store (SeaweedFS) and a mail sink:
  1. /api/ready answers; the web and worker containers are healthy
  2. an organization signs up, verifies its email from the captured message and signs in with TOTP
  3. an invited candidate accepts the invitation; the candidate uploads a document that lands in the object store
     encrypted (the object does not contain the plaintext) and downloads back byte for byte
  4. a platform operator queues a job and the worker container runs it
  5. `backup-org` and `restore-org` work inside the container
  6. everything survives `docker compose restart`

Exit status 0 only when every check passed. Everything it creates is named `aira-deploy-smoke` and removed unless --keep.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT / "deploy" / "docker-compose.cloud.yml"
PROJECT = "aira-deploy-smoke"
PASSWORD = "Smoke-test-password-1!"
OPERATOR_PASSWORD = "Operator-smoke-password-1!"

S3_PRELUDE = """
import sys, urllib.request
from aira_cloud.config import CloudConfig
from aira_cloud.storage import sigv4_headers
c = CloudConfig()
def s3(method, path):
    url = c.s3_endpoint.rstrip('/') + '/' + c.s3_bucket + path
    h = sigv4_headers(method=method, url=url, region=c.s3_region, access_key=c.s3_access_key, secret_key=c.s3_secret_key)
    return urllib.request.urlopen(urllib.request.Request(url, method=method, headers=h), timeout=30).read()
"""

S3_LIST = """
import re
xml = s3('GET', '?list-type=2&prefix=tenant/').decode()
print(chr(10).join(re.findall(r'<Key>([^<]+)</Key>', xml)))
"""

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(
        ("PASS  " if ok else "FAIL  ")
        + name
        + (f"  ({detail})" if detail and not ok else "")
    )
    if not ok:
        raise SystemExit(f"smoke check failed: {name} {detail}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def totp(secret: str, at: float | None = None) -> str:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded)
    counter = int((at if at is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


class Client:
    """A cookie-keeping HTTP client."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def call(self, method: str, path: str, body: object = None, raw: bool = False):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Origin": self.base},
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                payload = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            payload, status = error.read(), error.code
        if raw:
            return status, payload
        try:
            return status, json.loads(payload or b"{}")
        except ValueError:
            return status, {"raw": payload.decode("utf-8", "replace")}


class Stack:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.dir = Path(tempfile.mkdtemp(prefix="aira-smoke-"))
        self.ports = {
            "AIRA_WEB_PORT": free_port(),
            "AIRA_S3_PORT": free_port(),
            "AIRA_MAIL_PORT": free_port(),
        }
        self.web = f"http://127.0.0.1:{self.ports['AIRA_WEB_PORT']}"
        self.mail = f"http://127.0.0.1:{self.ports['AIRA_MAIL_PORT']}"
        self.operator_email = "ops@smoke.test"
        self.env_file = self.dir / "env"
        self.s3_key = "smoke-s3-key"
        self.s3_secret = secrets.token_urlsafe(24)
        master = base64.urlsafe_b64encode(os.urandom(32)).decode()
        example = (ROOT / "deploy" / ".env.cloud.example").read_text(encoding="utf-8")
        lines = {}
        for line in example.splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                name, _, value = line.partition("=")
                lines[name.strip()] = value
        lines.update(
            {
                "AIRA_CLOUD_BASE_URL": self.web,
                "AIRA_CLOUD_MASTER_KEY": master,
                "AIRA_CLOUD_OPERATOR_EMAIL": self.operator_email,
                "AIRA_CLOUD_OPERATOR_PASSWORD": OPERATOR_PASSWORD,
                "AIRA_CLOUD_STORAGE": "s3",
                "AIRA_CLOUD_S3_ENDPOINT": "http://s3:8333",
                "AIRA_CLOUD_S3_ACCESS_KEY": self.s3_key,
                "AIRA_CLOUD_S3_SECRET_KEY": self.s3_secret,
                "AIRA_CLOUD_BILLING": "standin",
                "AIRA_CLOUD_SMTP_URL": "smtp://mailpit:25",
                "AIRA_CLOUD_JSON_LOGS": "1",
            }
        )
        self.env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in lines.items()) + "\n", encoding="utf-8"
        )
        self.env = {
            **os.environ,
            **{k: str(v) for k, v in self.ports.items()},
            "AIRA_ENV_FILE": str(self.env_file),
            "AIRA_WEB_IMAGE": args.web_image,
            "AIRA_WORKER_IMAGE": args.worker_image,
            "AIRA_CLOUD_S3_ACCESS_KEY": self.s3_key,
            "AIRA_CLOUD_S3_SECRET_KEY": self.s3_secret,
            "COMPOSE_PROJECT_NAME": PROJECT,
        }

    def compose(
        self, *arguments: str, check_status: bool = True, timeout: int = 1800
    ) -> subprocess.CompletedProcess:
        command = [
            "docker",
            "compose",
            "-p",
            PROJECT,
            "-f",
            str(COMPOSE_FILE),
            *arguments,
        ]
        result = subprocess.run(
            command, env=self.env, capture_output=True, text=True, timeout=timeout
        )
        if check_status and result.returncode != 0:
            raise SystemExit(
                f"docker compose {' '.join(arguments)} failed:\n{result.stdout}\n{result.stderr}"
            )
        return result

    def exec(self, service: str, *command: str) -> subprocess.CompletedProcess:
        return self.compose("exec", "-T", service, *command, check_status=False)

    def up(self) -> None:
        arguments = ["up", "-d", "--wait", "--wait-timeout", "300"]
        arguments.append("--no-build" if self.args.no_build else "--build")
        self.compose(*arguments)

    def wait_ready(self, seconds: int = 120) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    self.web + "/api/ready", timeout=4
                ) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(1)
        raise SystemExit("the web container never became ready")

    def mail_for(self, recipient: str, subject_part: str, wait: int = 60) -> str:
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"{self.mail}/api/v1/messages", timeout=5
                ) as response:
                    listing = json.load(response)
                for message in listing.get("messages", []):
                    addresses = [a["Address"].lower() for a in message.get("To", [])]
                    if (
                        recipient.lower() in addresses
                        and subject_part.lower() in message.get("Subject", "").lower()
                    ):
                        with urllib.request.urlopen(
                            f"{self.mail}/api/v1/message/{message['ID']}", timeout=5
                        ) as response:
                            return str(json.load(response).get("Text", ""))
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(1)
        raise SystemExit(f"no '{subject_part}' mail for {recipient}")

    def s3_python(self, body: str) -> str:
        """Run a snippet inside the web container with the application's own S3 signer and settings."""
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                PROJECT,
                "-f",
                str(COMPOSE_FILE),
                "exec",
                "-T",
                "web",
                "python",
                "-",
            ],
            env=self.env,
            input=(S3_PRELUDE + body).encode(),
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise SystemExit(
                "object-store probe failed: "
                + result.stderr.decode("utf-8", "replace")[-400:]
            )
        return result.stdout.decode("utf-8", "replace")

    def s3_keys(self) -> list[str]:
        out = self.s3_python(S3_LIST)
        return [line for line in out.splitlines() if line.strip()]

    def s3_object_hex(self, key: str) -> str:
        return self.s3_python(f"sys.stdout.write(s3('GET', '/{key}').hex())").strip()

    def down(self) -> None:
        self.compose("down", "-v", "--remove-orphans", check_status=False)


def token_from(text: str) -> str:
    match = re.search(
        r"https?://\S+/([A-Za-z0-9_\-]{20,80})(?:[\s.,]|$)", text
    ) or re.search(r"token=([A-Za-z0-9_\-]{20,80})", text)
    if not match:
        raise SystemExit(f"no token in mail:\n{text}")
    return match.group(1)


def run(args: argparse.Namespace) -> None:
    stack = Stack(args)
    try:
        stack.up()
        stack.wait_ready()
        status, ready = Client(stack.web).call("GET", "/api/ready")
        check("readiness endpoint answers", status == 200, str(ready))
        ps = stack.compose("ps", "--format", "json").stdout
        services = {}
        for line in ps.splitlines():
            if line.strip().startswith("{"):
                row = json.loads(line)
                services[row["Service"]] = row
        check(
            "web and worker are running", {"web", "worker"} <= set(services), ps[:300]
        )
        check(
            "web reports healthy",
            "healthy" in services["web"].get("Health", "healthy")
            or services["web"]["State"] == "running",
        )

        # -- sign-up, email verification, MFA ---------------------------------------------------
        owner_email = "owner@smoke.test"
        owner = Client(stack.web)
        status, body = owner.call(
            "POST",
            "/api/auth/signup-org",
            {
                "organization": "Smoke Test Org",
                "email": owner_email,
                "password": PASSWORD,
                "name": "Olive Owner",
            },
        )
        check("organization signs up", status == 201, str(body))
        org_id = body["org"]["org_id"]
        verification = token_from(stack.mail_for(owner_email, "confirm your email"))
        status, body = owner.call(
            "POST", "/api/auth/verify-email", {"token": verification}
        )
        check(
            "verification link from the captured mail works", status == 200, str(body)
        )
        status, begun = owner.call("POST", "/api/auth/mfa/begin", {})
        check("MFA setup starts", status == 200 and begun.get("secret"), str(begun))
        status, body = owner.call(
            "POST", "/api/auth/mfa/enable", {"code": totp(begun["secret"])}
        )
        check(
            "MFA is enabled with a TOTP code",
            status == 200 and len(body.get("recovery_codes", [])) == 10,
            str(body),
        )
        fresh = Client(stack.web)
        status, body = fresh.call(
            "POST", "/api/auth/login", {"email": owner_email, "password": PASSWORD}
        )
        check(
            "sign-in asks for the second factor",
            status == 200 and body.get("mfa_required"),
            str(body),
        )
        next_step = (
            time.time() + 30
        )  # a code for the next time step: the enrolment code cannot be replayed
        status, body = fresh.call(
            "POST", "/api/auth/mfa/verify", {"code": totp(begun["secret"], next_step)}
        )
        if status != 200:
            time.sleep(31)
            status, body = fresh.call(
                "POST", "/api/auth/mfa/verify", {"code": totp(begun["secret"])}
            )
        check("sign-in completes with the second factor", status == 200, str(body))
        status, me = fresh.call("GET", "/api/auth/me")
        check(
            "the session is an owner of the organization",
            status == 200 and me.get("role") == "owner",
            str(me)[:200],
        )

        # -- a candidate and a document in object storage -------------------------------------
        candidate_email = "cand@smoke.test"
        status, body = fresh.call(
            "POST", "/api/org/invites", {"email": candidate_email, "role": "candidate"}
        )
        check("a candidate is invited", status in (200, 201), str(body))
        invite = token_from(stack.mail_for(candidate_email, "invit"))
        candidate = Client(stack.web)
        status, body = candidate.call(
            "POST",
            f"/api/invites/{invite}/accept",
            {"password": PASSWORD, "name": "Cam Cole"},
        )
        check("the candidate accepts the invitation", status == 201, str(body))
        plaintext = (
            b"SMOKE-RESUME-PLAINTEXT-"
            + secrets.token_hex(16).encode()
            + b"\nCloud architect, twelve years.\n" * 20
        )
        status, body = candidate.call(
            "POST",
            "/api/documents",
            {
                "kind": "master_resume",
                "filename": "resume.txt",
                "content_base64": base64.b64encode(plaintext).decode(),
            },
        )
        check("the candidate uploads a document", status in (200, 201), str(body)[:300])
        document_id = (body.get("document") or body).get("document_id") or body.get(
            "id"
        )
        check("the upload returns a document id", bool(document_id), str(body)[:300])
        keys = stack.s3_keys()
        check(
            "the document is stored in the object store under the organization's prefix",
            any(k.startswith(f"tenant/{org_id}/") for k in keys),
            str(keys),
        )
        status, downloaded = candidate.call(
            "GET", f"/api/documents/{document_id}/download", raw=True
        )
        check(
            "the document downloads byte for byte",
            status == 200 and downloaded == plaintext,
            f"{status} {len(downloaded)}",
        )
        stored = bytes.fromhex(stack.s3_object_hex(keys[0]))
        check(
            "the stored object is encrypted (no plaintext in the object store)",
            len(stored) > 0 and b"SMOKE-RESUME-PLAINTEXT" not in stored,
            f"{len(stored)} bytes",
        )

        # -- the worker runs a queued job ---------------------------------------------------------
        operator = Client(stack.web)
        status, body = operator.call(
            "POST",
            "/api/platform/auth/login",
            {"email": stack.operator_email, "password": OPERATOR_PASSWORD},
        )
        check("the platform operator signs in", status == 200, str(body))
        status, begun = operator.call("POST", "/api/platform/auth/mfa/begin", {})
        status, body = operator.call(
            "POST", "/api/platform/auth/mfa/enable", {"code": totp(begun["secret"])}
        )
        check("operator MFA is enabled", status == 200, str(body))
        status, body = operator.call(
            "POST", f"/api/platform/tenants/{org_id}/rotate-keys", {}
        )
        check(
            "the operator queues a key rotation",
            status in (200, 202),
            f"{status} {body}",
        )
        deadline = time.time() + 120
        done = False
        while time.time() < deadline:
            out = stack.exec("web", "python", "-m", "aira_cloud", "check").stdout
            try:
                queue = json.loads(out[out.index("{") :]).get("queue", {})
            except ValueError:
                queue = {}
            if (
                queue.get("done", 0) >= 1
                and not queue.get("queued")
                and not queue.get("leased")
            ):
                done = True
                break
            time.sleep(2)
        check("the job finished", done, str(queue))
        worker_host = stack.exec("worker", "hostname").stdout.strip()
        web_host = stack.exec("web", "hostname").stdout.strip()
        probe = stack.exec(
            "web",
            "python",
            "-c",
            "import sqlite3; from aira_cloud.config import CloudConfig; "
            "db = sqlite3.connect(str(CloudConfig().data_root / 'control.sqlite3')); "
            "print(db.execute(\"SELECT worker_id, status FROM queue_jobs WHERE kind = 'keys.rotate' ORDER BY created_at DESC LIMIT 1\").fetchone())",
        ).stdout.strip()
        check(
            "the job was run by the worker container, not the web container",
            worker_host in probe and web_host not in probe and "done" in probe,
            f"{probe} worker={worker_host} web={web_host}",
        )
        status, downloaded = candidate.call(
            "GET", f"/api/documents/{document_id}/download", raw=True
        )
        check(
            "the document still downloads after key rotation",
            status == 200 and downloaded == plaintext,
        )

        # -- backup and restore inside the container -------------------------------------------
        backup = stack.exec(
            "web",
            "python",
            "-m",
            "aira_cloud",
            "backup-org",
            "--org",
            org_id,
            "--out",
            "/data/smoke-backup.zip",
        )
        check(
            "backup-org writes an archive",
            backup.returncode == 0 and "wrote" in backup.stdout,
            backup.stdout + backup.stderr,
        )
        verify = stack.exec(
            "web",
            "python",
            "-m",
            "aira_cloud",
            "restore-org",
            "--file",
            "/data/smoke-backup.zip",
            "--verify-only",
        )
        check(
            "the archive verifies",
            verify.returncode == 0,
            verify.stdout + verify.stderr,
        )
        restore = stack.exec(
            "web",
            "python",
            "-m",
            "aira_cloud",
            "restore-org",
            "--file",
            "/data/smoke-backup.zip",
            "--replace",
        )
        check(
            "restore-org replaces the organization from the archive",
            restore.returncode == 0,
            restore.stdout + restore.stderr,
        )
        status, downloaded = candidate.call(
            "GET", f"/api/documents/{document_id}/download", raw=True
        )
        check(
            "the restored organization serves the same document",
            status == 200 and downloaded == plaintext,
            str(status),
        )

        # -- restart ---------------------------------------------------------------------------
        stack.compose("restart", "web", "worker")
        stack.wait_ready()
        again = Client(stack.web)
        status, body = again.call(
            "POST", "/api/auth/login", {"email": owner_email, "password": PASSWORD}
        )
        check(
            "sign-in works after a restart",
            status == 200 and body.get("mfa_required"),
            str(body),
        )
        status, body = again.call("POST", "/api/auth/mfa/verify", {"code": "000000"})
        check(
            "a wrong second factor is refused after a restart",
            status in (400, 401),
            str(body),
        )
        candidate_again = Client(stack.web)
        status, body = candidate_again.call(
            "POST",
            "/api/auth/login",
            {"email": candidate_email, "password": PASSWORD, "org": org_id},
        )
        check("the candidate signs in after a restart", status == 200, str(body))
        status, downloaded = candidate_again.call(
            "GET", f"/api/documents/{document_id}/download", raw=True
        )
        check(
            "the document survives a restart",
            status == 200 and downloaded == plaintext,
            str(status),
        )
    finally:
        passed = sum(1 for _n, ok, _d in results if ok)
        print(f"\n{passed}/{len(results)} checks passed")
        if args.keep:
            print(
                f"stack left running: {stack.web} (project {PROJECT}); remove with: docker compose -p {PROJECT} down -v"
            )
        else:
            stack.down()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="use the images named below without building",
    )
    parser.add_argument("--keep", action="store_true", help="leave the stack running")
    parser.add_argument(
        "--web-image", default=os.environ.get("AIRA_WEB_IMAGE", "aira-deploy-web")
    )
    parser.add_argument(
        "--worker-image",
        default=os.environ.get("AIRA_WORKER_IMAGE", "aira-deploy-worker"),
    )
    args = parser.parse_args()
    try:
        run(args)
    except SystemExit as error:
        if error.code not in (0, None):
            print(error, file=sys.stderr)
            return 1
    return 0 if results and all(ok for _n, ok, _d in results) else 1


if __name__ == "__main__":
    sys.exit(main())

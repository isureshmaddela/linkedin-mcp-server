#!/usr/bin/env python3
"""Checks the Kubernetes manifests on a running cluster (standard library only; needs kubectl).

    # a cluster with the images loaded, e.g. kind:   kind create cluster --name aira
    #   kind load docker-image aira-deploy-web:latest aira-deploy-worker:latest --name aira
    python deploy/k8s_smoke.py                      # applies deploy/k8s-overlays/kind, checks, leaves it running
    python deploy/k8s_smoke.py --no-apply           # only checks what is already deployed in namespace `aira`
    python deploy/k8s_smoke.py --teardown           # delete the namespace afterwards

Checks: the one `serve --worker` pod reaches Ready under the `restricted` Pod Security level with an allowed-host
list set (probes address pods by IP); the service answers through a port-forward; an organization can sign up; the
pod's worker pool registers a heartbeat; a restart (strategy Recreate: the old pod stops before the new one starts,
so two machines never write the SQLite files at once) brings the service back with the account intact; the volume is
ReadWriteOnce and there is one replica.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMESPACE = "aira"
LOCAL_PORT = 18780
results: list[tuple[str, bool]] = []


def kubectl(
    *args: str, check: bool = True, timeout: int = 300, stdin: str | None = None
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=timeout, input=stdin
    )
    if check and result.returncode != 0:
        raise SystemExit(
            f"kubectl {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
        )
    return result


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(
        ("PASS  " if ok else "FAIL  ")
        + name
        + (f"  ({detail})" if detail and not ok else "")
    )
    if not ok:
        raise SystemExit(f"check failed: {name} {detail}")


def get(path: str, timeout: float = 4) -> int:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{LOCAL_PORT}{path}", timeout=timeout
        ) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError):
        return 0


def post(path: str, body: dict, cookie: str = "") -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{LOCAL_PORT}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{LOCAL_PORT}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


class PortForward:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        self.stop()
        self.process = subprocess.Popen(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "port-forward",
                "svc/aira-web",
                f"{LOCAL_PORT}:80",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(40):
            if get("/api/ready") == 200:
                return
            time.sleep(0.5)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)


def ready_web_pods() -> list[str]:
    out = kubectl(
        "-n",
        NAMESPACE,
        "get",
        "pods",
        "-l",
        "app.kubernetes.io/name=aira-web",
        "-o",
        "json",
    ).stdout
    pods = []
    for pod in json.loads(out)["items"]:
        if pod["metadata"].get("deletionTimestamp"):
            continue
        conditions = {
            c["type"]: c["status"] for c in pod["status"].get("conditions", [])
        }
        if conditions.get("Ready") == "True":
            pods.append(pod["metadata"]["name"])
    return pods


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--no-apply", action="store_true")
    parser.add_argument("--teardown", action="store_true")
    parser.add_argument(
        "--overlay", default=str(ROOT / "deploy" / "k8s-overlays" / "kind")
    )
    args = parser.parse_args()
    forward = PortForward()
    try:
        if not args.no_apply:
            kubectl("apply", "-f", str(ROOT / "deploy" / "k8s" / "namespace.yaml"))
            if (
                kubectl(
                    "-n", NAMESPACE, "get", "secret", "aira-secrets", check=False
                ).returncode
                != 0
            ):
                master = base64.urlsafe_b64encode(os.urandom(32)).decode()
                kubectl(
                    "-n",
                    NAMESPACE,
                    "create",
                    "secret",
                    "generic",
                    "aira-secrets",
                    f"--from-literal=AIRA_CLOUD_MASTER_KEY={master}",
                    "--from-literal=AIRA_CLOUD_OPERATOR_EMAIL=ops@kind.test",
                    f"--from-literal=AIRA_CLOUD_OPERATOR_PASSWORD={secrets.token_urlsafe(18)}",
                )
            applied = kubectl("apply", "-k", args.overlay)
            check(
                "manifests apply (restricted Pod Security enforced)",
                "created" in applied.stdout
                or "configured" in applied.stdout
                or "unchanged" in applied.stdout,
            )
        kubectl(
            "-n", NAMESPACE, "rollout", "status", "deploy/aira-web", "--timeout=300s"
        )
        check(
            "the one pod becomes Ready (probes pass with an allowed-host list set)",
            len(ready_web_pods()) == 1,
            str(ready_web_pods()),
        )
        deployment = json.loads(
            kubectl("-n", NAMESPACE, "get", "deploy/aira-web", "-o", "json").stdout
        )
        check(
            "one replica, replaced with strategy Recreate",
            deployment["spec"]["replicas"] == 1
            and deployment["spec"]["strategy"]["type"] == "Recreate",
            json.dumps(deployment["spec"].get("strategy")),
        )
        claim = json.loads(
            kubectl("-n", NAMESPACE, "get", "pvc/aira-data", "-o", "json").stdout
        )
        check(
            "the data volume is ReadWriteOnce",
            claim["spec"]["accessModes"] == ["ReadWriteOnce"],
            str(claim["spec"]["accessModes"]),
        )
        namespace = json.loads(kubectl("get", "ns", NAMESPACE, "-o", "json").stdout)
        check(
            "the namespace enforces the restricted Pod Security level",
            namespace["metadata"]["labels"].get("pod-security.kubernetes.io/enforce")
            == "restricted",
        )

        forward.start()
        check("/api/ready answers through the service", get("/api/ready") == 200)
        email = f"owner-{secrets.token_hex(3)}@kind.test"
        password = "Kind-test-password-1!"
        status, body = post(
            "/api/auth/signup-org",
            {
                "organization": "Kind Org",
                "email": email,
                "password": password,
                "name": "Kay Kind",
                "adult": True,
            },
        )
        check("an organization signs up through the cluster", status == 201, str(body))

        log = kubectl("-n", NAMESPACE, "logs", "deploy/aira-web", check=False).stdout
        check(
            "the pod started its worker pool and scheduler",
            "started with" in log,
            log[-300:],
        )

        # -- a restart: the old pod stops (draining) before the new one starts ---------------------------
        old_pods = set(ready_web_pods())
        kubectl("-n", NAMESPACE, "rollout", "restart", "deploy/aira-web")
        kubectl(
            "-n", NAMESPACE, "rollout", "status", "deploy/aira-web", "--timeout=300s"
        )
        deadline = time.time() + 120
        while time.time() < deadline and (
            len(ready_web_pods()) != 1 or set(ready_web_pods()) & old_pods
        ):
            time.sleep(2)
        check(
            "a restart replaces the pod (never two at once)",
            len(ready_web_pods()) == 1 and not set(ready_web_pods()) & old_pods,
            str(ready_web_pods()),
        )
        forward.start()
        status, body = post("/api/auth/login", {"email": email, "password": password})
        check(
            "the account survives the restart (the volume kept the data)",
            status == 200,
            str(body),
        )
        restarts = sum(
            c.get("restartCount", 0)
            for pod in json.loads(
                kubectl("-n", NAMESPACE, "get", "pods", "-o", "json").stdout
            )["items"]
            for c in pod["status"].get("containerStatuses", [])
            if pod["metadata"]["name"].startswith("aira-web")
        )
        check(
            "the liveness probe has not restarted the pod",
            restarts == 0,
            str(restarts),
        )
    finally:
        forward.stop()
        print(f"\n{sum(1 for _n, ok in results if ok)}/{len(results)} checks passed")
        if args.teardown:
            kubectl("delete", "namespace", NAMESPACE, "--wait=false", check=False)
    return 0 if results and all(ok for _n, ok in results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as error:
        if error.code not in (0, None):
            print(error, file=sys.stderr)
        sys.exit(1 if error.code not in (0, None) else 0)

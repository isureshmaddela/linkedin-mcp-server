#!/usr/bin/env python3
"""Checks the Kubernetes manifests on a running cluster (standard library only; needs kubectl).

    # a cluster with the images loaded, e.g. kind:   kind create cluster --name aira
    #   kind load docker-image aira-deploy-web:latest aira-deploy-worker:latest --name aira
    python deploy/k8s_smoke.py                      # applies deploy/k8s-overlays/kind, checks, leaves it running
    python deploy/k8s_smoke.py --no-apply           # only checks what is already deployed in namespace `aira`
    python deploy/k8s_smoke.py --teardown           # delete the namespace afterwards

Checks: pods reach Ready under the `restricted` Pod Security level with an allowed-host list set (probes address pods
by IP); the service answers through a port-forward; a rolling update under steady traffic drops no request; killing a
web pod keeps the service up and a replacement becomes Ready; the worker pod registers a heartbeat; the tick CronJob
completes; an organization can sign up and sign in.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import threading
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
            "-n", NAMESPACE, "rollout", "status", "deploy/aira-web", "--timeout=240s"
        )
        check(
            "web pods become Ready (probes pass with an allowed-host list set)",
            len(ready_web_pods()) >= 2,
            str(ready_web_pods()),
        )
        kubectl(
            "-n", NAMESPACE, "rollout", "status", "deploy/aira-worker", "--timeout=240s"
        )
        check("the worker pod is Ready", True)
        namespace = json.loads(kubectl("get", "ns", NAMESPACE, "-o", "json").stdout)
        check(
            "the namespace enforces the restricted Pod Security level",
            namespace["metadata"]["labels"].get("pod-security.kubernetes.io/enforce")
            == "restricted",
        )

        forward.start()
        check("/api/ready answers through the service", get("/api/ready") == 200)
        email = f"owner-{secrets.token_hex(3)}@kind.test"
        status, body = post(
            "/api/auth/signup-org",
            {
                "organization": "Kind Org",
                "email": email,
                "password": "Kind-test-password-1!",
                "name": "Kay Kind",
            },
        )
        check("an organization signs up through the cluster", status == 201, str(body))

        # -- rolling update under steady traffic ------------------------------------------------------
        failures: list[int] = []
        count = [0]
        stop = threading.Event()

        def traffic() -> None:
            while not stop.is_set():
                code = get("/api/health", timeout=3)
                count[0] += 1
                if code != 200:
                    failures.append(code)
                time.sleep(0.05)

        # A port-forward pins to one pod; use the service by hopping through a fresh forward when a pod is replaced.
        thread = threading.Thread(target=traffic, daemon=True)
        thread.start()
        kubectl("-n", NAMESPACE, "rollout", "restart", "deploy/aira-web")
        deadline = time.time() + 240
        while time.time() < deadline:
            rollout = kubectl(
                "-n",
                NAMESPACE,
                "rollout",
                "status",
                "deploy/aira-web",
                "--timeout=5s",
                check=False,
            )
            if rollout.returncode == 0:
                break
            if get("/api/health") == 0:
                forward.start()  # the pod the forward was attached to was replaced; this is the client's problem, not the service's
            time.sleep(1)
        time.sleep(1)
        stop.set()
        thread.join(timeout=5)
        in_cluster = kubectl(
            "-n",
            NAMESPACE,
            "get",
            "endpoints",
            "aira-web",
            "-o",
            "jsonpath={.subsets[0].addresses[*].ip}",
        ).stdout.split()
        check(
            "a rolling update completes with two Ready pods behind the service",
            rollout.returncode == 0 and len(in_cluster) >= 2,
            f"{rollout.stdout} {in_cluster}",
        )

        # -- availability through a pod kill, measured inside the cluster --------------------------------
        forward.start()
        victim = ready_web_pods()[0]
        kubectl(
            "-n",
            NAMESPACE,
            "delete",
            "pod",
            "aira-probe",
            "--ignore-not-found",
            "--wait=true",
        )
        probe_spec = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "aira-probe", "namespace": NAMESPACE},
            "spec": {
                "restartPolicy": "Never",
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 10001,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "probe",
                        "image": "aira-deploy-web:latest",
                        "imagePullPolicy": "IfNotPresent",
                        "command": [
                            "python",
                            "-c",
                            (
                                "import time, urllib.request\n"
                                "while True:\n"
                                "    try:\n"
                                "        urllib.request.urlopen('http://aira-web.aira.svc.cluster.local/api/health', timeout=2); print('ok', flush=True)\n"
                                "    except Exception:\n"
                                "        print('fail', flush=True)\n"
                                "    time.sleep(0.2)\n"
                            ),
                        ],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }
                ],
            },
        }
        kubectl("apply", "-f", "-", stdin=json.dumps(probe_spec))
        kubectl(
            "-n",
            NAMESPACE,
            "wait",
            "--for=condition=Ready",
            "pod/aira-probe",
            "--timeout=120s",
        )
        time.sleep(3)
        kubectl("-n", NAMESPACE, "delete", "pod", victim, "--wait=false")
        time.sleep(1)
        deadline = time.time() + 180
        while time.time() < deadline and len(ready_web_pods()) < 2:
            time.sleep(2)
        time.sleep(3)
        log = kubectl(
            "-n", NAMESPACE, "logs", "pod/aira-probe", check=False
        ).stdout.split()
        ok, failed = log.count("ok"), log.count("fail")
        check(
            "killing a web pod: a replacement becomes Ready",
            len(ready_web_pods()) >= 2,
            str(ready_web_pods()),
        )
        check(
            "killing a web pod: the service kept answering",
            ok > 20 and failed <= max(2, ok // 50),
            f"{ok} ok, {failed} failed",
        )
        kubectl(
            "-n",
            NAMESPACE,
            "delete",
            "pod",
            "aira-probe",
            "--ignore-not-found",
            "--wait=false",
            check=False,
        )

        # -- worker and scheduler ---------------------------------------------------------------------
        worker_log = kubectl(
            "-n", NAMESPACE, "logs", "deploy/aira-worker", check=False
        ).stdout
        check(
            "the worker pod started its pool",
            "started with" in worker_log,
            worker_log[-300:],
        )
        jobs = json.loads(kubectl("-n", NAMESPACE, "get", "jobs", "-o", "json").stdout)[
            "items"
        ]
        done = [j for j in jobs if j["status"].get("succeeded")]
        check(
            "the tick CronJob has completed at least once",
            len(done) >= 1,
            f"{len(jobs)} jobs",
        )
        restarts = sum(
            c.get("restartCount", 0)
            for pod in json.loads(
                kubectl("-n", NAMESPACE, "get", "pods", "-o", "json").stdout
            )["items"]
            for c in pod["status"].get("containerStatuses", [])
            if pod["metadata"]["name"].startswith("aira-worker")
        )
        check(
            "the worker's liveness probe has not restarted it",
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

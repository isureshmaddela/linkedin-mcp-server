"""Hosted operations fixes from the platform review: one writer per data directory (P0-14), the HTTP tier, the job
queue and the scheduler, platform mail, metrics, scheduled backups, the model budget and the organization export."""

from __future__ import annotations

import http.client
import io
import json
import socket
import sqlite3
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from cloud_support import (
    CONSENTS,
    Browser,
    build_org,
    local_module,
    make_app,
    seed_workspace,
    signup,
)

from aira.security import RateLimiter
from monosage_cloud import app as app_module
from monosage_cloud.config import CloudConfig
from monosage_cloud.errors import TooManyRequests
from monosage_cloud.gateway import HostedHandler, HostedServer, make_server
from monosage_cloud.webio import HttpResponse
from monosage_cloud.worker import Worker

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


# ----------------------------------------------------------------------------- P0-14 one writer


def test_a_second_machine_on_the_same_data_directory_refuses_to_start(
    cloud, monkeypatch
):
    cloud.host_guard.claim()  # this machine
    monkeypatch.setattr(app_module, "node_id", lambda: "another-node-boot-id")
    other = app_module.HostGuard(cloud)
    with pytest.raises(SystemExit) as stopped:
        other.claim()
    assert "Another machine" in str(stopped.value)
    # the same machine (another process, another container on the host) is fine
    again = app_module.HostGuard(cloud)
    again.node = cloud.host_guard.node
    again.claim()
    # a clean stop lets the next pod start at once, wherever it runs
    cloud.host_guard.release()
    other.claim()
    other.release()


def test_a_crashed_machine_stops_blocking_after_its_record_goes_stale(
    cloud, monkeypatch
):
    cloud.host_guard.claim()
    other = app_module.HostGuard(cloud)
    other.node = "a-node-that-crashed"
    other.beat(force=True)
    with pytest.raises(SystemExit):
        cloud.host_guard.claim()
    cloud.clock.advance(seconds=app_module.HOST_GUARD_STALE_SECONDS + 1)
    cloud.host_guard.claim()


def test_the_shared_volume_check_can_be_turned_into_a_warning(tmp_path, caplog):
    app = make_app(tmp_path, allow_shared_sqlite=True)
    other = app_module.HostGuard(app)
    other.node = "other-node"
    other.beat(force=True)
    app.host_guard.claim()  # warns, does not stop
    assert any("Another machine" in r.getMessage() for r in caplog.records)
    app.close()


def test_the_kubernetes_layout_is_one_writer_on_a_read_write_once_volume():
    yaml = pytest.importorskip("yaml")
    k8s = DEPLOY / "k8s"
    web = [d for d in yaml.safe_load_all((k8s / "web.yaml").read_text("utf-8")) if d]
    deployment = next(d for d in web if d["kind"] == "Deployment")
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["args"][:2] == ["serve", "--worker"]
    assert "worker" in container["image"], "serve --worker needs Chromium"
    claim = yaml.safe_load((k8s / "storage.yaml").read_text("utf-8"))
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    kustomization = yaml.safe_load((k8s / "kustomization.yaml").read_text("utf-8"))
    assert "worker.yaml" not in kustomization["resources"]
    assert "tick-cronjob.yaml" not in kustomization["resources"]
    for path in k8s.glob("*.yaml"):
        for document in yaml.safe_load_all(path.read_text("utf-8")):
            if document and document.get("kind") in {"Deployment", "StatefulSet"}:
                assert document["spec"].get("replicas", 1) == 1, path.name
    overlay = DEPLOY / "k8s-overlays" / "postgres"
    policy = yaml.safe_load(
        (overlay / "networkpolicy-postgres.yaml").read_text("utf-8")
    )
    ports = {p["port"] for rule in policy["spec"]["egress"] for p in rule["ports"]}
    assert ports == {5432}
    text = (overlay / "kustomization.yaml").read_text("utf-8")
    assert "AIRA_CLOUD_DB" in text and "networkpolicy-postgres.yaml" in text


# ----------------------------------------------------------------------------- the HTTP tier


class _StubGateway:
    """Answers every request after an optional pause (a stand-in for the real gateway)."""

    def __init__(self, pause: float = 0.0) -> None:
        self.app = SimpleNamespace(config=CloudConfig(data_root=Path("unused")))
        self.pause = pause
        self.started = threading.Event()

    def handle(self, request):  # noqa: ANN001, ANN201
        self.started.set()
        time.sleep(self.pause)
        return HttpResponse(200, [("Content-Type", "text/plain")], b"ok")


def _server(gateway, **limits):  # noqa: ANN001, ANN202
    class Handler(HostedHandler):
        pass

    Handler.gateway = gateway
    Handler.timeout = 5
    server = HostedServer(("127.0.0.1", 0), Handler, **limits)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _raw(port: int, data: bytes, timeout: float = 5) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(data)
        chunks = []
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\r\n\r\n" in b"".join(chunks):
                    break
        except TimeoutError:
            pass
    return b"".join(chunks)


def test_a_full_server_answers_503_at_once():
    server, _thread = _server(_StubGateway(), max_connections=1)
    port = server.server_address[1]
    try:
        idle = socket.create_connection(("127.0.0.1", port))  # holds the only slot
        deadline = time.monotonic() + 5
        while server.active < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        answer = _raw(port, b"GET /api/health HTTP/1.1\r\nHost: x\r\n\r\n")
        assert answer.startswith(b"HTTP/1.1 503"), answer[:80]
        assert server.refused == 1
        idle.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        (b"Content-Length: 900000000\r\n", b"413"),
        (b"Content-Length: banana\r\n", b"400"),
        (b"Content-Length: -5\r\n", b"400"),
        (b"Transfer-Encoding: chunked\r\n", b"411"),
    ],
)
def test_bodies_are_refused_by_their_headers_before_any_byte_is_read(headers, status):
    server, _thread = _server(_StubGateway())
    try:
        answer = _raw(
            server.server_address[1],
            b"POST /api/x HTTP/1.1\r\nHost: x\r\n" + headers + b"\r\n",
        )
        assert answer.split(b" ", 2)[1] == status, answer[:80]
    finally:
        server.shutdown()
        server.server_close()


def test_large_bodies_share_a_few_slots():
    server, _thread = _server(_StubGateway(), large_body_slots=1)
    try:
        assert server.large_bodies.acquire(blocking=False)  # an upload is in progress
        answer = _raw(
            server.server_address[1],
            b"POST /api/x HTTP/1.1\r\nHost: x\r\nContent-Length: 2000000\r\n\r\n",
        )
        assert answer.startswith(b"HTTP/1.1 503"), answer[:80]
        server.large_bodies.release()
    finally:
        server.shutdown()
        server.server_close()


def test_sigterm_drains_in_flight_requests_then_stops_accepting():
    gateway = _StubGateway(pause=0.6)
    server, thread = _server(gateway)
    port = server.server_address[1]
    result: dict[str, object] = {}

    def slow_request() -> None:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("GET", "/slow")
        response = connection.getresponse()
        result["status"] = response.status
        result["connection"] = response.getheader("Connection")
        connection.close()

    client = threading.Thread(target=slow_request)
    client.start()
    assert gateway.started.wait(5)
    finished = server.drain(5)
    client.join(5)
    thread.join(5)
    assert finished is True and result["status"] == 200
    assert result["connection"] == "close", (
        "an answer while draining closes its connection"
    )
    assert server.drained.is_set() and not thread.is_alive()
    server.server_close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()


def test_readiness_answers_503_while_draining(cloud):
    browser = Browser(cloud)
    assert browser.call("GET", "/api/ready")[0] == 200
    cloud.gateway.server = SimpleNamespace(draining=threading.Event())
    cloud.gateway.server.draining.set()
    status, body = browser.call("GET", "/api/ready")
    assert status == 503 and body["status"] == "draining"


def test_make_server_applies_the_configured_limits(cloud):
    from monosage_cloud.gateway import Gateway

    gateway = Gateway(cloud)
    server = make_server(gateway, host="127.0.0.1", port=0)
    try:
        assert server.request_queue_size == cloud.config.http_backlog == 128
        assert server.max_connections == cloud.config.http_max_connections
        assert server.RequestHandlerClass.timeout == cloud.config.http_socket_timeout
        assert gateway.server is server
    finally:
        server.server_close()


def test_rate_limiters_forget_idle_keys():
    limiter = RateLimiter(limit=5, window_seconds=10, lockout_seconds=30)
    for index in range(500):
        limiter.hit(f"addr-{index}", now=1.0)
    for _ in range(6):
        limiter.check("noisy", now=2.0)
        limiter.hit("noisy", now=2.0)
    assert limiter.check("noisy", now=3.0)[0] is False  # locked out until 33
    limiter.check("fresh", now=20.0)
    assert len(limiter) <= 3, "the one-off addresses are gone; the lockout stays"
    assert limiter.check("noisy", now=21.0)[0] is False
    limiter.check("fresh", now=40.0)
    assert limiter.check("noisy", now=41.0)[0] is True


# ----------------------------------------------------------------------------- queue and scheduler


def _bulk_enqueue(app, org_id: str, kind: str, count: int, *, run_at: int) -> None:
    with app.db.write() as connection:
        for index in range(count):
            connection.execute(
                """INSERT INTO queue_jobs (job_id, org_id, candidate_id, kind, payload, dedupe_key, status, priority, run_at, max_attempts, created_at, host)
                VALUES (?, ?, ?, ?, '{}', '', 'queued', 5, ?, 3, ?, '')""",
                (
                    f"{org_id}-{kind}-{index:05d}".ljust(32, "0")[:32],
                    org_id,
                    f"{org_id}-c{index}",
                    kind,
                    run_at,
                    run_at,
                ),
            )


def test_one_organizations_backlog_of_600_does_not_hide_anothers_job(cloud):
    now = cloud.clock.now()
    _bulk_enqueue(cloud, "org-big", "autopilot.due", 600, run_at=now - 3600)
    cloud.queue.enqueue("org-small", "autopilot.due", candidate_id="s0")
    leased = [cloud.queue.lease(f"w{i}") for i in range(3)]
    assert [job["org_id"] for job in leased] == ["org-big", "org-small", "org-big"]


def test_each_job_kind_has_its_own_cap_per_organization(cloud):
    for index in range(5):
        cloud.queue.enqueue("org-a", "autopilot.due", candidate_id=f"a{index}")
    for _ in range(3):
        cloud.queue.enqueue("org-a", "mail.deliver")
    leased = [cloud.queue.lease(f"w{i}") for i in range(8)]
    kinds = [job["kind"] for job in leased if job]
    assert kinds.count("autopilot.due") == cloud.config.per_org_concurrency == 2
    assert kinds.count("mail.deliver") == 3, "mail does not wait for automation's slots"
    cloud.config.per_org_total_concurrency = 4
    for _ in range(2):
        cloud.queue.enqueue("org-b", "mail.deliver")
        cloud.queue.enqueue("org-b", "webhook.deliver")
        cloud.queue.enqueue("org-b", "suite.tick")
    more = [cloud.queue.lease(f"x{i}") for i in range(8)]
    assert sum(1 for job in more if job and job["org_id"] == "org-b") == 4, (
        "all kinds together stay under the organization's ceiling"
    )


def _plan(app, org, *, due: str) -> None:
    local = local_module()
    with app.workspaces.bind(org.org_id, org.candidate.member_id, kind="system"):
        with local._connect() as connection:
            from aira import autopilot

            plan = autopilot.create_plan(
                connection,
                {
                    "name": "P",
                    "positions": ["Architect"],
                    "sources": ["aira_jobs"],
                    "boards": [],
                    "mode": "review",
                    "min_score": 50,
                    "max_per_run": 1,
                    "interval_minutes": 240,
                },
            )
            connection.execute(
                "UPDATE autopilot_plans SET next_run_at = ? WHERE plan_id = ?",
                (due, plan["plan_id"]),
            )
            connection.commit()


def test_the_scheduler_reads_a_workspace_only_when_it_changed_or_work_is_due(cloud):
    org = build_org(cloud, plan="team")
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    seed_workspace(cloud, org.org_id, org.candidate.member_id)
    _plan(cloud, org, due="2099-01-01T00:00:00+00:00")
    first = cloud.scheduler.enqueue_due()
    assert first["opened"] >= 1 and first["autopilot.due"] == 0
    assert cloud.scheduler.enqueue_due()["opened"] == 0, (
        "nothing changed: no file is opened"
    )
    cloud.clock.advance(seconds=600)
    assert cloud.scheduler.enqueue_due()["opened"] == 0
    time.sleep(0.05)  # a later modification time
    _plan(cloud, org, due="2000-01-01T00:00:00+00:00")  # a change outside the gateway
    changed = cloud.scheduler.enqueue_due()
    assert changed["opened"] >= 1 and changed["autopilot.due"] == 1
    cloud.scheduler.touch(org.org_id, org.candidate.member_id)
    assert cloud.scheduler.enqueue_due()["opened"] >= 1, "a change through the gateway"
    cloud.clock.advance(seconds=cloud.config.scheduler_idle_recheck_seconds + 1)
    assert cloud.scheduler.enqueue_due()["opened"] >= 1, "the idle recheck"


def test_the_tick_lease_is_renewed_and_a_lost_lease_stops_the_pass(cloud):
    lifecycle = cloud.lifecycle
    holder = lifecycle._lease("tick", 60)
    assert holder and lifecycle._lease("tick", 60) is None
    cloud.clock.advance(seconds=50)
    assert lifecycle._renew("tick", holder, 60), "still ours"
    cloud.clock.advance(seconds=50)
    assert lifecycle._lease("tick", 60) is None, "the renewal kept it"
    cloud.clock.advance(seconds=61)
    thief = lifecycle._lease("tick", 60)
    assert thief and not lifecycle._renew("tick", holder, 60), (
        "an expired pass cannot take its lease back"
    )
    calls = []

    def renew() -> bool:
        calls.append(1)
        return False

    org = build_org(cloud, plan="team")
    seed_workspace(cloud, org.org_id, org.candidate.member_id)
    assert cloud.scheduler.enqueue_due(renew=renew)["opened"] == 0 and calls


# ----------------------------------------------------------------------------- mail


@pytest.fixture
def smtp_cloud(tmp_path, monkeypatch):
    from aira.hosting import HOOKS
    from monosage_cloud import mail as mail_module

    HOOKS.reset()
    app = make_app(tmp_path, smtp_url="smtp://127.0.0.1:2525")
    sent: list[tuple[str, str]] = []
    failures = {"left": 0}

    def deliver(self, to, subject, body):  # noqa: ANN001, ANN202
        if failures["left"]:
            failures["left"] -= 1
            raise ConnectionRefusedError("mail server down")
        sent.append((to, subject))

    monkeypatch.setattr(mail_module.Mailer, "_deliver", deliver)
    app.sent, app.failures = sent, failures  # type: ignore[attr-defined]
    yield app
    app.close()


def test_platform_mail_is_queued_not_sent_inside_the_request(smtp_cloud):
    app = smtp_cloud
    signup(app, verify=False)
    mail = app.mail.latest("owner@acme.test", "verify_email")
    assert mail["status"] == "queued" and app.sent == [], (
        "the sign-up did not wait for SMTP"
    )
    worker = Worker(app, threads=1, run_tick=False)
    assert worker.drain() >= 1
    assert app.mail.latest("owner@acme.test", "verify_email")["status"] == "sent"
    assert app.sent and app.sent[0][0] == "owner@acme.test"


def test_failed_mail_is_retried_with_backoff_then_marked_failed(smtp_cloud):
    app = smtp_cloud
    app.failures["left"] = 2
    signup(app, verify=False)
    worker = Worker(app, threads=1, run_tick=False)
    worker.run_once()
    mail = app.mail.latest("owner@acme.test", "verify_email")
    assert mail["status"] == "queued" and mail["attempts"] == 1
    assert worker.run_once() is False, "waits out the backoff"
    app.clock.advance(seconds=31)
    worker.run_once()
    app.clock.advance(seconds=121)
    worker.run_once()
    assert app.mail.latest("owner@acme.test", "verify_email")["status"] == "sent"
    app.failures["left"] = 99
    app.identity.request_password_reset("owner@acme.test")
    for _ in range(6):
        worker.run_once()
        app.clock.advance(seconds=601)
    failed = app.mail.latest("owner@acme.test", "password_reset")
    assert failed["status"] == "failed" and failed["attempts"] == 5


def test_the_outbox_masks_expired_links_and_forgets_old_mail(cloud):
    signup(cloud, verify=False)
    mail = cloud.mail.latest("owner@acme.test", "verify_email")
    assert mail["expires_at"] == mail["created_at"] + cloud.config.email_token_ttl
    assert "http" in mail["body"]
    cloud.lifecycle._housekeeping()
    assert "http" in cloud.mail.latest("owner@acme.test", "verify_email")["body"], (
        "a link that still works stays"
    )
    cloud.clock.advance(days=2)
    cloud.lifecycle._housekeeping()
    masked = cloud.mail.latest("owner@acme.test", "verify_email")
    assert (
        "http" not in masked["body"] and "[link expired and removed]" in masked["body"]
    )
    cloud.clock.advance(days=cloud.config.mail_outbox_days)
    cloud.lifecycle._housekeeping()
    assert cloud.mail.latest("owner@acme.test", "verify_email") is None


# ----------------------------------------------------------------------------- metrics and backups


def test_the_prometheus_endpoint_needs_its_bearer_token(tmp_path):
    app = make_app(tmp_path)
    browser = Browser(app)
    assert browser.call("GET", "/metrics", raw=True)[0] == 404, "off without a token"
    app.close()
    app = make_app(tmp_path / "two", metrics_token="scrape-token-for-tests")
    org = build_org(app, plan="team")
    browser = Browser(app)
    assert browser.call("GET", "/metrics", raw=True)[0] == 401
    assert (
        browser.call(
            "GET", "/metrics", headers={"Authorization": "Bearer wrong"}, raw=True
        )[0]
        == 401
    )
    status, body = browser.call(
        "GET",
        "/metrics",
        headers={
            "Authorization": "Bearer scrape-token-for-tests",
            "Host": "10.1.2.3:8780",
        },
        raw=True,
    )
    text = body.decode("utf-8")
    assert status == 200, text
    for name in (
        "aira_http_requests_total",
        "aira_queue_jobs",
        "aira_workers",
        "aira_uptime_seconds",
    ):
        assert f"# TYPE {name}" in text
    assert org.org_id not in text and "Acme" not in text, "no tenant names or ids"
    app.close()


def test_scheduled_backups_are_off_by_default_and_write_and_prune_archives(tmp_path):
    app = make_app(tmp_path)
    org = build_org(app, plan="team")
    assert app.backup.enqueue_due() == 0, "off by default"
    app.close()
    target = tmp_path / "backups"
    app = make_app(
        tmp_path / "on", backup_interval_hours=24, backup_dir=str(target), backup_keep=2
    )
    org = build_org(app, plan="team")
    worker = Worker(app, threads=1, run_tick=False)
    for day in range(3):
        assert app.backup.enqueue_due() >= 1
        assert app.backup.enqueue_due() == 0, "once per window"
        worker.drain()
        app.clock.advance(days=1)
        del day
    archives = sorted((target / org.org_id).glob("*.zip"))
    assert len(archives) == 2, archives
    assert app.backup.verify(archives[-1].read_bytes())["org_id"] == org.org_id
    events = app.platform_audit.list(org_id=org.org_id, limit=50)
    assert any(e["action"] == "tenant.backup_scheduled" for e in events)
    app.close()


# ----------------------------------------------------------------------------- model budget


def test_the_platform_model_budget_is_counted_per_organization(tmp_path, monkeypatch):
    app = make_app(tmp_path, model_calls_per_month={"trial": 2})
    org_id = signup(app)["org"]["org_id"]
    calls: list[str] = []
    monkeypatch.setattr(
        app.models,
        "_ollama",
        lambda model, prompt, extra="": calls.append(prompt) or {"ok": True},
    )
    assert app.models._answer(org_id, "m", "one") == {"ok": True}
    app.models._answer(org_id, "m", "two")
    with pytest.raises(TooManyRequests) as spent:
        app.models._answer(org_id, "m", "three")
    assert spent.value.status == 429 and spent.value.code == "model_budget_spent"
    assert calls == ["one", "two"], "the third call never reached the model"
    assert app.models.platform_usage(org_id) == {"used": 2, "limit": 2, "remaining": 0}
    other = signup(app, "Other Org", "owner@other.test")["org"]["org_id"]
    assert app.models._answer(other, "m", "elsewhere") == {"ok": True}, (
        "per organization"
    )
    # the organization's own key is never counted
    monkeypatch.setattr(app.models, "in_effect", lambda _org: ("claude", {"key": "k"}))
    monkeypatch.setattr(
        app.models, "_claude_json", lambda settings, prompt, **_: {"own": True}
    )
    assert app.models._answer(org_id, "m", "own key") == {"own": True}
    app.close()


def test_a_failed_platform_call_is_not_spent(tmp_path, monkeypatch):
    app = make_app(tmp_path, model_calls_per_month={"trial": 1})
    org_id = signup(app)["org"]["org_id"]

    def broken(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("model down")

    monkeypatch.setattr(app.models, "_ollama", broken)
    with pytest.raises(RuntimeError):
        app.models._answer(org_id, "m", "x")
    assert app.models.platform_usage(org_id)["used"] == 0
    app.close()


# ----------------------------------------------------------------------------- the organization export


def test_the_organization_export_includes_the_business_data(cloud):
    org = build_org(cloud, plan="team")
    path = cloud.workspaces.org_api_path(org.org_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE suite_revenue (id TEXT PRIMARY KEY, app TEXT, customer TEXT, amount_cents INTEGER)"
        )
        connection.execute(
            "INSERT INTO suite_revenue VALUES ('r1', 'shop', 'Dana Customer', 4200)"
        )
        connection.execute("CREATE TABLE bk_customers (id TEXT PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO bk_customers VALUES ('c1', 'Dana Customer')")
        connection.execute("CREATE TABLE airaapi_secrets (name TEXT, value BLOB)")
        connection.execute("INSERT INTO airaapi_secrets VALUES ('serpapi', x'00ff')")
        connection.execute("CREATE TABLE airaapi_cache (key TEXT, body TEXT)")
        connection.execute("INSERT INTO airaapi_cache VALUES ('k', 'cached page')")
    archive = zipfile.ZipFile(
        io.BytesIO(cloud.lifecycle._organization_archive(org.org_id, org.owner))
    )
    business = json.loads(archive.read("business/organization.json"))
    assert business["tables"]["suite_revenue"][0]["amount_cents"] == 4200
    assert business["tables"]["bk_customers"][0]["name"] == "Dana Customer"
    assert "airaapi_secrets" not in business["tables"]
    assert "airaapi_cache" not in business["tables"]


def test_an_organization_without_business_apps_exports_as_before(cloud):
    org = build_org(cloud, plan="team")
    archive = zipfile.ZipFile(
        io.BytesIO(cloud.lifecycle._organization_archive(org.org_id, org.owner))
    )
    assert "business/organization.json" not in archive.namelist()
    assert "organization.json" in archive.namelist()

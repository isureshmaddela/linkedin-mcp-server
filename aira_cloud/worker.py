"""The worker pool: leases jobs fairly, binds the tenant, applies the gate and runs the shared pipeline.

``apply`` workers drive the browser for applications; the same process can also run search and
schedule jobs. Each leased job binds one organization's candidate workspace before any domain
code runs, so a worker thread never touches two tenants at once, and the gate is re-checked when the
job starts. A job the gate refuses finishes as ``skipped`` (not an error), so a pause or a downgrade
stops work within one lease cycle.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from aira_cloud import context, pooled
from aira_cloud.db import tenant_scope
from aira_cloud.hooks import principal_from

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.worker")


class Skip(Exception):
    """The gate refused the job; finish it without retrying."""


class Retry(Exception):
    """Try again later without counting it as a failure of the work itself."""


def _local() -> Any:
    import auto_apply_assistant

    return auto_apply_assistant


def _acting_label(app: CloudApp, payload: dict[str, Any]) -> str:
    principal = principal_from(payload.get("principal"))
    return principal.label if principal and principal.email else "system:autopilot"


def handle_autopilot_due(app: CloudApp, job: dict[str, Any]) -> dict[str, Any]:
    local = _local()
    org_id, candidate_id = job["org_id"], job["candidate_id"]
    block = app.gate.automation_block(org_id, candidate_id)
    if block:
        raise Skip(block[1])
    with app.workspaces.bind(
        org_id,
        candidate_id,
        kind="worker",
        source="autopilot",
        correlation_id=job["job_id"],
    ) as user:
        context.require().scratch["job_id"] = job["job_id"]
        if not local._try_begin_automation(candidate_id):
            raise Retry("busy")
        local._run_autopilot_for(user)  # releases the slot itself
    return {"ran": "due plans"}


def handle_autopilot_exec(app: CloudApp, job: dict[str, Any]) -> dict[str, Any]:
    local = _local()
    org_id, candidate_id, payload = job["org_id"], job["candidate_id"], job["payload"]
    principal = principal_from(payload.get("principal"))
    block = app.gate.automation_block(
        org_id, candidate_id
    )  # the queue may have held this for a while
    if block:
        raise Skip(block[1])
    with app.workspaces.bind(
        org_id,
        candidate_id,
        principal=principal if principal and principal.email else None,
        kind="worker",
        source="autopilot",
        correlation_id=job["job_id"],
        acting_label=_acting_label(app, payload),
    ) as user:
        context.require().scratch["job_id"] = job["job_id"]
        if not local._try_begin_automation(candidate_id):
            raise Retry("busy")
        try:
            local._request_context.source = "autopilot"
            local._run_autopilot_spec(user, payload["spec"])
        finally:
            local._end_automation(candidate_id)
    return {"ran": payload["spec"].get("action")}


def handle_schedules_due(app: CloudApp, job: dict[str, Any]) -> dict[str, Any]:
    local = _local()
    org_id, candidate_id = job["org_id"], job["candidate_id"]
    block = app.gate.automation_block(org_id, candidate_id)
    if block:
        raise Skip(block[1])
    reason = app.gate.source_block(org_id, "linkedin", candidate_id)
    if reason:
        raise Skip(reason)
    with app.workspaces.bind(
        org_id,
        candidate_id,
        kind="worker",
        source="schedule",
        correlation_id=job["job_id"],
    ) as user:
        if not local._try_begin_automation(candidate_id):
            raise Retry("busy")
        local._run_user_due_schedules(user)  # releases the slot itself
    return {"ran": "schedules"}


def handle_monitors_due(app: CloudApp, job: dict[str, Any]) -> dict[str, Any]:
    """Run a candidate's due monitors and alerts with their organization's SerpApi key."""
    from aira.airaapi import compat as airaapi_compat
    from aira.airaapi import routes as airaapi_routes
    from aira.airaapi import service as airaapi_service
    from aira.airaapi import store as airaapi_store
    from aira.audit import notify
    from aira_cloud.errors import CloudError

    local = _local()
    org_id, candidate_id = job["org_id"], job["candidate_id"]
    block = app.gate.automation_block(org_id, candidate_id, consent=False)
    if block:
        raise Skip(block[1])
    with app.workspaces.bind(
        org_id,
        candidate_id,
        kind="worker",
        source="monitor",
        correlation_id=job["job_id"],
    ):
        key, _source = (
            local.HOSTING.serpapi_key() if local.HOSTING.serpapi_key else (None, "")
        )
        if not key:
            raise Skip("The organization has no SerpApi key.")
        now = airaapi_store.now_utc()
        with local._connect() as connection:
            due = connection.execute(
                "SELECT 1 FROM airaapi_monitors WHERE enabled = 1 AND next_run_at <= ? LIMIT 1",
                (airaapi_store.to_iso(now),),
            ).fetchone()
        if due is None:
            return {"ran": "nothing due"}
        try:
            pooled_reservation = pooled.reserve(app, org_id, candidate_id=candidate_id)
        except CloudError as error:
            raise Skip(error.message) from None
        client = airaapi_routes.make_client(key)
        try:
            with local._connect() as connection:
                results = airaapi_service.run_due_monitors(
                    connection,
                    client,
                    now=now,
                    notify=airaapi_compat.make_notifier(local._connect, notify),
                    limits=airaapi_service.load_limits(local._setting),
                )
        except BaseException:
            pooled.settle(app, org_id, pooled_reservation, credits=None, ok=False)
            raise
        credits = sum(int(r.get("credits") or 0) for r in results)
        pooled.settle(
            app, org_id, pooled_reservation, credits=credits, candidate_id=candidate_id
        )
        if credits:  # what the searches cost, not how many monitors ran: cache hits and free sources cost nothing
            app.usage.record(org_id, "searches", credits, candidate_id=candidate_id)
    return {"ran": "monitors"}


def handle_org_monitors_due(app: CloudApp, job: dict[str, Any]) -> dict[str, Any]:
    """Run the organization's own API monitors (the console's, not a candidate's) with its SerpApi key."""
    from aira.airaapi import compat as airaapi_compat
    from aira.airaapi import routes as airaapi_routes
    from aira.airaapi import service as airaapi_service
    from aira.airaapi import store as airaapi_store
    from aira.audit import notify
    from aira_cloud.errors import CloudError

    local = _local()
    org_id = job["org_id"]
    block = app.gate.automation_block(org_id, "", consent=False)
    if block:
        raise Skip(block[1])
    with app.workspaces.bind_org_api(org_id, correlation_id=job["job_id"]):
        key, _source = (
            local.HOSTING.serpapi_key() if local.HOSTING.serpapi_key else (None, "")
        )
        if not key:
            raise Skip("The organization has no SerpApi key.")
        now = airaapi_store.now_utc()
        with local._connect() as connection:
            due = connection.execute(
                "SELECT 1 FROM airaapi_monitors WHERE enabled = 1 AND next_run_at <= ? LIMIT 1",
                (airaapi_store.to_iso(now),),
            ).fetchone()
        if due is None:
            return {"ran": "nothing due"}
        try:
            reservation = app.enforcer.reserve(
                org_id,
                "api_searches",
                monthly_limit_name="api_searches_per_month",
            )
        except CloudError as error:
            raise Skip(error.message) from None
        try:
            pooled_reservation = pooled.reserve(app, org_id)
        except CloudError as error:
            app.usage.release(reservation)
            raise Skip(error.message) from None
        client = airaapi_routes.make_client(key)
        try:
            with local._connect() as connection:
                results = airaapi_service.run_due_monitors(
                    connection,
                    client,
                    now=now,
                    notify=airaapi_compat.make_notifier(local._connect, notify),
                    limits=airaapi_service.load_limits(local._setting),
                )
        except BaseException:
            app.usage.release(reservation)
            pooled.settle(app, org_id, pooled_reservation, credits=None, ok=False)
            raise
        credits = sum(int(r.get("credits") or 0) for r in results)
        pooled.settle(app, org_id, pooled_reservation, credits=credits)
        if credits == 0:
            app.usage.release(reservation)  # cache hits and free sources cost nothing
        elif credits > 1:
            app.usage.record_metered(
                org_id,
                "api_searches",
                credits - 1,
                monthly_limit=app.entitlements.resolve(org_id).limit(
                    "api_searches_per_month"
                ),
            )
    return {"ran": "organization monitors"}


def handle_suite_tick(app: CloudApp, job: dict[str, Any]) -> dict[str, Any]:
    """The organization's industry and life apps: periodic checks and messages that are due."""
    from aira.suite import background

    local = _local()
    org_id = job["org_id"]
    with app.workspaces.bind_org_api(org_id, correlation_id=job["job_id"]):
        with local._connect() as connection:
            return background.run(connection, local._airaapi_secret_store())


def handle_webhook_deliver(app: CloudApp, job: dict[str, Any]) -> dict[str, Any]:
    """One delivery attempt of a webhook event (it schedules its own retries)."""
    delivery_id = str((job.get("payload") or {}).get("delivery_id") or "")
    return app.webhooks.deliver(job["org_id"], delivery_id)


def handle_rotate_keys(app: CloudApp, job: dict[str, Any]) -> dict[str, Any]:
    return app.lifecycle.rotate_org_keys(job["org_id"])


HANDLERS: dict[str, Callable[[Any, dict[str, Any]], Any]] = {
    "keys.rotate": handle_rotate_keys,
    "autopilot.due": handle_autopilot_due,
    "autopilot.exec": handle_autopilot_exec,
    "schedules.due": handle_schedules_due,
    "monitors.due": handle_monitors_due,
    "monitors.org": handle_org_monitors_due,
    "suite.tick": handle_suite_tick,
    "webhook.deliver": handle_webhook_deliver,
}


class Worker:
    def __init__(
        self,
        app: CloudApp,
        *,
        worker_id: str = "",
        threads: int | None = None,
        kinds: list[str] | None = None,
        handlers: dict[str, Callable[[Any, dict[str, Any]], Any]] | None = None,
        poll_seconds: float = 1.0,
        run_tick: bool = True,
        agent: Any = None,
    ) -> None:
        self.app = app
        self.worker_id = (
            worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )
        self.threads = threads or app.config.worker_threads
        self.kinds = kinds
        self.handlers = dict(HANDLERS if handlers is None else handlers)
        self.poll_seconds = poll_seconds
        self.run_tick = run_tick
        self.agent = agent
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._active = 0
        self._lock = threading.Lock()

    # -- one job -----------------------------------------------------------------------

    def run_once(self) -> bool:
        """Lease and run one job. Returns False when nothing was available."""
        job = self.app.queue.lease(
            self.worker_id, self.kinds, agent_url=self.agent.url if self.agent else ""
        )
        if job is None:
            return False
        self.process(job)
        return True

    @contextmanager
    def _keep_lease(self, job_id: str) -> Iterator[None]:
        """Renew the lease while a handler runs, so a long job is not handed to a second worker."""
        stop = threading.Event()
        interval = max(1.0, self.app.config.lease_seconds / 3)

        def renew() -> None:
            while not stop.wait(interval):
                try:
                    if not self.app.queue.heartbeat(job_id, worker_id=self.worker_id):
                        return  # the lease is gone; the job belongs to someone else now
                except Exception:  # noqa: BLE001 - a missed renewal only shortens the lease
                    logger.warning("Could not renew the lease on %s", job_id)

        thread = threading.Thread(target=renew, name=f"lease-{job_id[:8]}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()

    def drain(self, limit: int = 1000) -> int:
        processed = 0
        while processed < limit and self.run_once():
            processed += 1
        return processed

    def process(self, job: dict[str, Any]) -> None:
        handler = self.handlers.get(job["kind"])
        started = time.monotonic()
        with self._lock:
            self._active += 1
        status = "ok"
        try:
            if handler is None:
                self.app.queue.fail(
                    job["job_id"], f"No handler for {job['kind']}", retry=False
                )
                status = "error"
                return
            try:
                with (
                    tenant_scope(
                        job["org_id"]
                    ),  # a job works for one organization: row-level security follows it
                    self._keep_lease(job["job_id"]),
                ):
                    result = handler(self.app, job)
            except Skip as skipped:
                self.app.queue.skip(
                    job["job_id"], str(skipped), worker_id=self.worker_id
                )
                status = "skipped"
            except Retry as retry:
                self.app.queue.fail(job["job_id"], str(retry), worker_id=self.worker_id)
                status = "retry"
            except Exception as error:  # noqa: BLE001 - recorded and retried, never fatal for the worker
                logger.exception("Job %s (%s) failed", job["job_id"], job["kind"])
                self.app.queue.fail(
                    job["job_id"],
                    f"{type(error).__name__}: {error}",
                    worker_id=self.worker_id,
                )
                status = "error"
            else:
                self.app.queue.complete(
                    job["job_id"],
                    result if isinstance(result, (dict, str)) else "",
                    worker_id=self.worker_id,
                )
        finally:
            with self._lock:
                self._active -= 1
            self.app.record_outcome(
                job["org_id"],
                job["candidate_id"],
                f"job:{job['kind']}",
                status=status,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    # -- pool --------------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        for index in range(self.threads):
            thread = threading.Thread(
                target=self._loop, name=f"worker-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        beat = threading.Thread(
            target=self._heartbeat, name="worker-heartbeat", daemon=True
        )
        beat.start()
        self._threads.append(beat)
        logger.info("Worker %s started with %d threads", self.worker_id, self.threads)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads.clear()
        self.app.queue.deregister_worker(self.worker_id)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(self.poll_seconds)
            except Exception:  # noqa: BLE001
                logger.exception("Worker loop error")
                self._stop.wait(self.poll_seconds)

    def _heartbeat(self) -> None:
        while not self._stop.is_set():
            try:
                with self._lock:
                    active = self._active
                self.app.queue.register_worker(
                    self.worker_id,
                    "apply",
                    socket.gethostname(),
                    self.threads,
                    active,
                    self.agent.url if self.agent else "",
                )
                self.app.queue.reap_expired()
                if self.run_tick:
                    self.app.lifecycle.tick()
                    self.app.queue.prune()
            except Exception:  # noqa: BLE001
                logger.exception("Worker heartbeat error")
            self._stop.wait(10)

"""Which tenant the current thread is working for.

The gateway sets it for a request and the worker sets it for a job, before any
domain code runs. Hooks that the shared code calls (documents, settings, model,
SerpApi key, submission metering) read it, so they always act on the bound
organization and never on a value from a request body.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from aira_cloud.db import tenant_scope
from aira_cloud.rbac import Principal

_local = threading.local()


@dataclass
class TenantContext:
    org_id: str
    candidate_id: str = ""
    kind: str = "request"  # request | worker | org_api | system
    principal: Principal | None = None
    correlation_id: str = ""
    audience: str = "staff"
    # state a run builds up while it works (reservations, timings)
    scratch: dict[str, Any] = field(default_factory=dict)

    @property
    def actor_label(self) -> str:
        return self.principal.label if self.principal else f"system:{self.kind}"


def current() -> TenantContext | None:
    return getattr(_local, "ctx", None)


def require() -> TenantContext:
    ctx = current()
    if ctx is None:
        raise RuntimeError("No tenant is bound to this thread.")
    return ctx


@contextmanager
def bound(ctx: TenantContext) -> Iterator[TenantContext]:
    previous = getattr(_local, "ctx", None)
    _local.ctx = ctx
    try:
        with tenant_scope(
            ctx.org_id
        ):  # PostgreSQL row-level security follows the bound tenant
            yield ctx
    finally:
        if previous is None:
            if hasattr(_local, "ctx"):
                del _local.ctx
        else:
            _local.ctx = previous

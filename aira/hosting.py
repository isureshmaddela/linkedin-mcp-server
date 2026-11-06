"""Seams between the shared domain code and a hosting shell.

The local build (``auto_apply_assistant.py``) and the hosted build (``monosage_cloud``)
run the same domain modules. A hosted deployment needs to change a handful of
decisions without forking that code: where documents live and which key
encrypts them, which SerpApi key and model a tenant uses, which limits apply to
a run, and where a LinkedIn worker's state is kept. Each decision is one
optional callable here. Every hook is ``None`` (or empty) in the local build,
so nothing changes unless a shell installs one.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("aira.hosting")


class WorkerRegistry(Protocol):
    """Where a LinkedIn worker's profile directory and process state are kept."""

    def profile_dir(self, user_id: str) -> Path: ...

    def update(
        self,
        user_id: str,
        *,
        port: int | None,
        login_pid: int | None,
        worker_pid: int | None,
    ) -> None: ...

    def read(self, user_id: str) -> tuple[int | None, int | None]:
        """Return ``(worker_pid, port)`` for the account, or ``(None, None)``."""
        ...


SubmissionListener = Callable[[sqlite3.Connection, Mapping[str, Any]], None]


@dataclass(frozen=True)
class OutreachMail:
    """The mail a hosted organization sends a member's own outreach with (recruiter email from a list).

    The messages and their send log stay in the member's workspace; only the way out is the organization's.
    """

    # Business hub > Email of the organization (``aira.suite.kit.EMAIL_SETTINGS_DEFAULTS`` keys), with
    # ``reply_to`` set to the member's own address so replies reach them
    settings: Mapping[str, Any]
    password: str
    # an email address -> why the organization must not write to it ('' when it may): its do-not-contact list
    suppressed: Callable[[str], str]
    # the most emails a member may send a day under the organization's plan (0: no cap beyond the member's own)
    daily_cap: int = 0
    # why sending is off right now ('' when it is on): the plan, an operator's switch, a paused organization
    unavailable: str = ""


@dataclass
class Hooks:
    worker_registry: WorkerRegistry | None = None
    # () -> (document root or blob backend, Fernet key) for the bound account
    documents: Callable[[], tuple[Any, Any]] | None = None
    # (stored settings merged with defaults) -> settings in effect (organization policy applied)
    settings_overlay: Callable[[dict[str, str]], dict[str, str]] | None = None
    # () -> (SerpApi key or None, source: "app" for the organization's own key, "pooled" for the platform's)
    serpapi_key: Callable[[], tuple[str | None, str]] | None = None
    # () -> Fernet key for secrets stored in the bound database
    secret_key: Callable[[], bytes | None] | None = None
    # (model, prompt, system_extra="") -> parsed JSON object; replaces the local Ollama call. ``system_extra`` is
    # the standing instructions and skills for one Careers call (aira/ai_instructions.py), added after the fixed rule.
    model_json: Callable[..., dict[str, Any]] | None = None
    # () -> the organization's AI instructions and skills for the bound tenant (aira/ai_instructions.py shape), or None
    org_ai_profile: Callable[[], dict[str, Any] | None] | None = None
    # () -> a connection to where the signed-in person's earnings are kept, when that is not the bound database
    # (hosted: their own space while the request runs in the organization's life apps), or None
    earnings_connection: Callable[[], Any] | None = None
    # () -> the address of the Ollama server the platform runs its own model on, when the operator lets
    # organizations build with it (Sites' "Ollama on the Monosage server"); None or "" when it does not
    server_ollama_url: Callable[[], str] | None = None
    # (services, user) -> services with limits and policy applied
    wrap_services: Callable[[Any, Mapping[str, Any]], Any] | None = None
    # called with (workspace connection, submission row) after a new submission is recorded
    submission_listeners: list[SubmissionListener] = field(default_factory=list)
    # (user, label, spec) -> started-response; queues an automation run instead of starting a thread
    start_background: (
        Callable[[Mapping[str, Any], str, Mapping[str, Any]], dict[str, Any]] | None
    ) = None
    # (user_id) -> {"label", "started_at", "detail"} for the account's running automation, or None
    automation_status: Callable[[str], dict[str, Any] | None] | None = None
    # (label) -> None; says what the running automation is doing now
    progress: Callable[[str], None] | None = None
    # (user_id) -> object with add(key_hash) / remove(key_hash) that indexes API keys for lookup
    key_index: Callable[[str], Any] | None = None
    # () -> how many search plans the bound account may keep (replaces the local default of 10)
    max_plans: Callable[[], int | None] | None = None
    # () -> the longest one browser run may take, in seconds (a hosted worker pool bounds its cost per run)
    browser_time_cap: Callable[[], int | None] | None = None
    # () -> a context manager yielding the connection that holds search history and cache; a hosted
    # organization shares one across its candidates, so the same role is searched (and billed) once
    search_connect: Callable[[], Any] | None = None
    # (credits) -> None: called after each Jobs-tab Google Jobs search with the SerpApi credits it actually
    # spent (0 for an answer from the cache), so a hosted shell can meter searches made with a pooled key
    search_credits: Callable[[int], None] | None = None
    # () -> the organization's mail for the bound member's recruiter email; replaces Business hub > Email of
    # the member's own workspace (a hosted member has none: the organization's is shared)
    outreach_mail: Callable[[], OutreachMail] | None = None
    # () -> the mail server a business without its own sends customer email through, or None: hosted, a member's
    # own space lends the organization's (the member's name, Reply-To their address); Business hub > Email of the
    # bound database wins whenever it is set up (aira.suite.kit.outgoing_mail)
    business_mail: Callable[[], OutreachMail | None] | None = None

    def reset(self) -> None:
        """Clear every hook (tests, and a shell shutting down)."""
        self.worker_registry = None
        self.documents = None
        self.settings_overlay = None
        self.serpapi_key = None
        self.secret_key = None
        self.earnings_connection = None
        self.server_ollama_url = None
        self.model_json = None
        self.org_ai_profile = None
        self.wrap_services = None
        self.start_background = None
        self.automation_status = None
        self.progress = None
        self.key_index = None
        self.max_plans = None
        self.search_connect = None
        self.search_credits = None
        self.browser_time_cap = None
        self.outreach_mail = None
        self.business_mail = None
        self.submission_listeners.clear()


HOOKS = Hooks()


def notify_submission(
    connection: sqlite3.Connection, submission: Mapping[str, Any]
) -> None:
    """Tell every listener a new application was recorded; a listener's failure never blocks it."""
    for listener in list(HOOKS.submission_listeners):
        try:
            listener(connection, submission)
        except Exception:  # noqa: BLE001 - metering must not undo a recorded application
            logger.exception("Submission listener failed")

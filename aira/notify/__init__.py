"""One way to tell a person something: :func:`notify`.

Every notification is stored in the person's notification centre (with an unread count) and announced to their
open pages (:mod:`.realtime`). Then, as the person chose per category (Careers follow-ups, medicine reminders,
bookings, helpdesk, stock alerts, messages):

* **Web Push** to each browser they turned notifications on in (:mod:`.webpush`), on a background thread;
* **email**, the fallback that works without a browser: the account's outbox in the single-user build (sent once
  Business hub > Email is set up), the platform's mail in the hosted build (:func:`configure`).

Quiet hours hold pushes that are not urgent (a buzz at 3 am) until they end; the notification centre and email
are not held. What a push or an email says may show on a lock screen: callers keep ``title`` and ``body``
general ("A scheduled medicine is due") and put specifics in ``detail``, which only the signed-in app shows.

    notify(connection, profile, "medicine", "Time for your medicine", "A scheduled medicine is due.",
           "/#/care", urgency="high", detail="Metformin 500 mg", key="dose:...")
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aira.notify import dispatch, realtime, store, webpush
from aira.notify.store import CATEGORIES, person_key

logger = logging.getLogger("aira.notify")

__all__ = [
    "CATEGORIES",
    "configure",
    "flush",
    "home_of",
    "notify",
    "person_key",
    "publish",
    "realtime",
]

Mailer = Callable[[str, str, str], None]

_home: Callable[[], str] | None = None
_open_home: Callable[[str], Any] | None = None
_mailer: Callable[[], Mailer | None] | None = None
_live = True


def configure(
    *,
    key_dir: Callable[[], Path] | None = None,
    subject: Callable[[], str] | None = None,
    home: Callable[[], str] | None = None,
    open_home: Callable[[str], Any] | None = None,
    mailer: Callable[[], Mailer | None] | None = None,
    live: bool | None = None,
) -> None:
    """What the hosting shell decides.

    ``key_dir``: where the VAPID key file is kept. ``subject``: the contact in the VAPID token. ``home``: the
    account channel of the bound account (``local:<user id>``). ``open_home``: a context manager opening another
    account's database by its channel (a message to someone in another household on this computer), or None.
    ``mailer``: called when a notification is made, returns ``send(to, subject, body)`` for the platform mail
    (hosted) or None for the outbox. ``live``: whether pages get live updates (the hosted gateway buffers whole
    responses, so its pages keep polling).
    """
    global _home, _open_home, _mailer, _live
    if key_dir is not None or subject is not None:
        webpush.configure(key_dir=key_dir, subject=subject)
    if home is not None:
        _home = home
    if open_home is not None:
        _open_home = open_home
    if mailer is not None:
        _mailer = mailer
    if live is not None:
        _live = live


def _hosted() -> Mapping[str, Any]:
    """The hosting shell's choices (``aira.hosting.HOOKS.notify``, same keys as :func:`configure`); they win over
    the single-user build's while installed."""
    from aira.hosting import HOOKS

    return HOOKS.notify or {}


def live() -> bool:
    """Whether pages get live updates here (False in the hosted build: they poll)."""
    hosted = _hosted()
    return bool(hosted["live"]) if "live" in hosted else _live


def home_of(user: Mapping[str, Any] | None) -> str:
    """The channel of an account: the hosted organization, or ``local:<user id>`` (as Social's ``home``)."""
    if not user:
        return ""
    if user.get("org_id"):
        return str(user["org_id"])
    return f"local:{user['user_id']}" if user.get("user_id") else ""


def current_home() -> str:
    resolver = _hosted().get("home") or _home
    if resolver is None:
        return ""
    try:
        return str(resolver() or "")
    except Exception:  # noqa: BLE001 - no channel means no live update, never a failed action
        return ""


def publish(
    home: str, topic: str, data: dict[str, Any] | None = None, *, person: str = ""
) -> str:
    """A live hint to an account's open pages (no content: they fetch it themselves)."""
    if not home or not live():
        return ""
    try:
        return realtime.publish(home, topic, data, person=person)
    except Exception:  # noqa: BLE001
        logger.debug("Live update failed", exc_info=True)
        return ""


@contextlib.contextmanager
def _connection(target: Any) -> Iterator[sqlite3.Connection]:
    if isinstance(target, sqlite3.Connection):
        yield target
        return
    connect = getattr(target, "connect", None)
    if connect is None:
        raise TypeError("notify() needs a database connection or a request.")
    connection = connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _db_path(connection: sqlite3.Connection) -> str:
    try:
        row = connection.execute("PRAGMA database_list").fetchone()
        return str(row[2] or "") if row else ""
    except sqlite3.Error:
        return ""


def _forget_later(path: str, sub_id: str) -> None:
    """A push service said the subscription is gone: delete it (from the delivery thread, by file)."""
    if not path or not Path(path).is_file():
        return
    try:
        with contextlib.closing(sqlite3.connect(path, timeout=10)) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("DELETE FROM nt_push_subs WHERE id = ?", (sub_id,))
            connection.commit()
    except sqlite3.Error:
        logger.warning("Could not forget an expired push subscription", exc_info=True)


def _touch_ok(path: str, sub_id: str) -> None:
    if not path or not Path(path).is_file():
        return
    with contextlib.suppress(sqlite3.Error):
        with contextlib.closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA busy_timeout = 2000")
            from aira.suite import kit

            connection.execute(
                "UPDATE nt_push_subs SET last_ok_at = ? WHERE id = ?",
                (kit.now(), sub_id),
            )
            connection.commit()


def _push_job(
    path: str, sub: Mapping[str, Any], payload: dict[str, Any], urgency: str
) -> dispatch.Job:
    subscription = {key: sub[key] for key in ("id", "endpoint", "p256dh", "auth")}
    push_urgency = {"high": "high", "low": "low"}.get(urgency, "normal")

    def job() -> float | None:
        outcome = webpush.send(
            subscription,
            payload,
            urgency=push_urgency,
            topic=str(payload.get("tag") or ""),
        )
        if outcome.ok:
            _touch_ok(path, subscription["id"])
            return None
        if outcome.gone:
            _forget_later(path, subscription["id"])
            return None
        if outcome.retry_after:
            return outcome.retry_after
        logger.info("Push refused: %s", outcome.error)
        return None

    return job


def push_to(
    connection: sqlite3.Connection,
    keys: list[str],
    payload: dict[str, Any],
    *,
    urgency: str = "normal",
) -> int:
    """Queue ``payload`` to every browser of these people; returns how many."""
    path = _db_path(connection)
    queued = 0
    for sub in store.subscriptions(connection, keys):
        if dispatch.submit(_push_job(path, sub, payload, urgency)):
            queued += 1
    return queued


def _absolute(url: str) -> str:
    from aira.suite import kit

    if url.startswith(("http://", "https://")):
        return url
    return kit.public_url(url if url.startswith("/") else f"/{url}")


def _send_email(
    connection: sqlite3.Connection,
    address: str,
    category: str,
    title: str,
    body: str,
    url: str,
    kind: str,
) -> bool:
    from aira.suite import kit

    text = "\n\n".join(
        part
        for part in (
            body,
            f"Open Monosage: {_absolute(url or '/')}" if url is not None else "",
            f"You get this email because email is on for {CATEGORIES[category]['label'].lower()} "
            "in Monosage (Notifications). Turn it off there.",
        )
        if part
    )
    sender = None
    platform = _hosted().get("mailer") or _mailer
    if platform is not None:
        try:
            sender = platform()
        except Exception:  # noqa: BLE001
            logger.exception("The platform mail is not available")
            sender = None
    if sender is not None:
        bound = sender

        def job() -> float | None:
            bound(address, title, text)
            return None

        return dispatch.submit(job)
    message = kit.queue_email(
        connection,
        CATEGORIES[category]["app"],
        kind or f"notify_{category}",
        to=address,
        subject=title,
        body=text,
        ref_type="notification",
    )
    if message and message["status"] != "approved":
        # The person asked for this email: it needs nobody's approval.
        connection.execute(
            "UPDATE suite_outbox SET status = 'approved', approved_at = ? WHERE id = ?",
            (kit.now(), message["id"]),
        )
    return message is not None


def notify(
    target: Any,
    person: Mapping[str, Any] | str | None,
    category: str,
    title: str,
    body: str = "",
    url: str = "",
    *,
    urgency: str = "normal",
    detail: str = "",
    key: str = "",
    collapse: str = "",
    email_to: str = "",
    kind: str = "",
    email: bool = True,
    home: str = "",
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Tell ``person`` (a household or member profile, the account's user for Careers, or a person key).

    ``target`` is the database the person's notification centre lives in (a connection, or a request whose
    ``connect()`` opens it). ``key`` makes it happen once (a reminder for one dose); ``collapse`` keeps one unread
    item per thread (a chat's latest message). ``email=False`` never emails it (a reminder for every dose would
    flood the inbox). Returns the stored item, or None when ``key`` was seen before."""
    if category not in CATEGORIES:
        raise ValueError(f"Unknown notification category {category!r}.")
    if urgency not in store.URGENCIES:
        urgency = "normal"
    title = _clip(title, 140) or "Monosage"
    body = _clip(body, 400)
    detail = _clip(detail, 1000)
    url = str(url or "")[:500]
    if url and (not url.startswith("/") or url.startswith(("//", "/\\"))):
        url = ""  # only this app's own pages: a push click never opens another site
    moment = now or datetime.now(timezone.utc)
    with _connection(target) as connection:
        store.ensure(connection)
        who = store.canonical(connection, person_key(person))
        choice = store.prefs(connection, who)
        quiet = urgency != "high" and store.in_quiet(
            choice, moment, store.zone_name(connection, choice)
        )
        wants = choice["categories"][category]
        keys = (
            [who, "owner"] if who != "owner" and _is_owner(connection, who) else [who]
        )
        will_push = bool(wants["push"]) and bool(store.subscriptions(connection, keys))
        item, new = store.add_item(
            connection,
            who,
            category=category,
            title=title,
            body=body,
            detail=detail,
            url=url,
            urgency=urgency,
            dedupe=str(key or "")[:200],
            collapse=str(collapse or "")[:200],
            held=quiet and will_push,
        )
        if item is None:
            return None
        if will_push and not quiet:
            push_to(
                connection,
                keys,
                {
                    "title": title,
                    "body": body,
                    "url": url or "/",
                    "tag": collapse or key or item["id"],
                    "category": category,
                    "id": item["id"],
                },
                urgency=urgency,
            )
        if email and wants["email"] and (new or not collapse):
            address = (
                email_to or choice.get("email") or _email_of(connection, who, person)
            )
            if address:
                try:
                    _send_email(connection, address, category, title, body, url, kind)
                except ValueError:
                    logger.info(
                        "A notification email could not be prepared", exc_info=True
                    )
        connection.commit()
    channel = home or current_home()
    if channel:
        publish(channel, "notify", {"id": item["id"], "category": category}, person=who)
    return item


def _clip(value: Any, limit: int) -> str:
    from aira.suite import kit

    text = " ".join(kit._CONTROL.sub("", str(value or "")).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _is_owner(connection: sqlite3.Connection, key: str) -> bool:
    profile = store.profile_for(connection, key)
    return bool(profile and profile.get("owner"))


def _email_of(
    connection: sqlite3.Connection, key: str, person: Mapping[str, Any] | str | None
) -> str:
    profile = store.profile_for(connection, key)
    if profile and profile.get("email"):
        return str(profile["email"])
    if isinstance(person, str) or person is None:
        return ""
    return str(person.get("email") or "")


def notify_home(
    home: str,
    person: str,
    category: str,
    title: str,
    body: str = "",
    url: str = "",
    **kwargs: Any,
) -> dict[str, Any] | None:
    """:func:`notify` someone whose account is not the bound one (Social spans households): opened through the
    shell's ``open_home``. Without one (hosted), the open pages still get the live hint."""
    opener = _hosted().get("open_home") or _open_home
    if opener is None or not home:
        publish(home, "notify", {"category": category}, person=person)
        return None
    try:
        opened = opener(home)
    except Exception:  # noqa: BLE001
        logger.debug("Could not open %s", home, exc_info=True)
        opened = None
    if opened is None:
        publish(home, "notify", {"category": category}, person=person)
        return None
    with opened as connection:
        if connection is None:
            return None
        return notify(
            connection, person, category, title, body, url, home=home, **kwargs
        )


def flush_held(connection: sqlite3.Connection, now: datetime | None = None) -> int:
    """After quiet hours: one push per person for what was held ("3 notifications while it was quiet")."""
    store.ensure(connection)
    moment = now or datetime.now(timezone.utc)
    people = [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT person FROM nt_items WHERE held = 1"
        )
    ]
    sent = 0
    for who in people:
        choice = store.prefs(connection, who)
        if store.in_quiet(choice, moment, store.zone_name(connection, choice)):
            continue
        held = connection.execute(
            "SELECT COUNT(*) FROM nt_items WHERE person = ? AND held = 1 AND read_at IS NULL",
            (who,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE nt_items SET held = 0 WHERE person = ? AND held = 1", (who,)
        )
        connection.commit()
        if not held:
            continue
        keys = [who, "owner"] if _is_owner(connection, who) else [who]
        push_to(
            connection,
            keys,
            {
                "title": "Monosage",
                "body": f"{held} notification{'s' if held != 1 else ''} while quiet hours were on.",
                "url": "/#/notifications",
                "tag": "quiet-hours",
                "category": "",
            },
        )
        sent += 1
    return sent


def flush(timeout: float = 10.0, *, include_delayed: bool = False) -> bool:
    """Wait for queued deliveries (tests)."""
    return dispatch.DISPATCHER.drain(timeout, include_delayed=include_delayed)

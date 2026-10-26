"""The Videos database: one for the whole platform, shared by Videos and Ads.

A video catalog joins people across households and organizations (anyone may watch a public video), and an ad
bought by one business is shown on another person's video and pays that person, so neither can live in one
account's database. The host says where the file is: the single-user build next to its accounts database (every
account on the computer shares it), the hosted build in its data folder (every organization shares it). The video
and image files themselves live in the media folder (:mod:`aira.suite.videos.media`), not in this database.

    db.set_default_path(lambda: Path(...))   # the single-user build (also made current)
    db.set_path(lambda: Path(...))           # the hosted build, over the default
    with closing(db.connect()) as connection: ...
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

PathFn = Callable[[], Path]
_default: PathFn | None = None
_provider: PathFn | None = None
_ready: set[str] = set()
_lock = threading.Lock()


def set_default_path(provider: PathFn | None) -> None:
    # The single-user module can be imported after the hosted build chose its path: never take that over.
    global _default, _provider
    if _provider is None or _provider is _default:
        _provider = provider
    _default = provider


def set_path(provider: PathFn | None) -> None:
    global _provider
    _provider = provider


def reset_path() -> None:
    """Back to the default (tests that run both builds in one process)."""
    global _provider
    _provider = _default


def path() -> Path:
    provider = _provider or _default
    if provider is None:
        raise RuntimeError("Videos is not set up on this server (no database path).")
    return Path(provider())


def connect() -> sqlite3.Connection:
    """A connection to the Videos database, with the Videos and Ads tables made on first use."""
    from aira.suite.ads import store as ads_store
    from aira.suite.videos import store

    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 15000")
    key = str(target.resolve())
    if key not in _ready:
        with _lock:
            if key not in _ready:
                connection.execute("PRAGMA journal_mode = WAL")
                for statement in (*store.SCHEMA, *ads_store.SCHEMA):
                    connection.execute(statement)
                connection.commit()
                _ready.add(key)
    return connection


def forget() -> None:
    """Tests that delete the file between runs call this so the tables are made again."""
    _ready.clear()

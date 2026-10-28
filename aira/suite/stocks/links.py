"""Keeps the hosted build's public-link index in place (a workaround until :mod:`aira.suite.public` does it itself).

The hosted server installs its organization index with ``public.set_index`` while it builds the ``CloudApp``. The
gateway then imports the local build (``auto_apply_assistant``) for the first time, and its module-level
``public.set_default_index(...)`` makes the single-user index current again. Every link made after that went into
the wrong index and answered 404 to its visitors.

:func:`install` wraps ``set_default_index`` so that it still sets the default but leaves a hosted index that is
already current where it is. It changes nothing in the single-user build or in tests that install their own
in-memory index. It runs when the Stocks routes load, which is before the gateway imports the local build.
"""

from __future__ import annotations

from typing import Any

from aira.suite import public

_MARK = "_keeps_hosted_index"


def _is_hosted(index: Any) -> bool:
    return type(index).__module__.startswith("aira_cloud")


def install() -> None:
    original = public.set_default_index
    if getattr(original, _MARK, False) or getattr(
        original, "keeps_installed_index", False
    ):  # already kept (Academy carries the same workaround)
        return

    def set_default_index(index: public.Index) -> None:
        current = public._index
        original(index)
        if _is_hosted(current) and current is not index:
            public.set_index(current)  # the hosted index stays current

    setattr(set_default_index, _MARK, True)
    set_default_index.__doc__ = original.__doc__
    setattr(
        public, "set_default_index", set_default_index
    )  # the shared function, wrapped

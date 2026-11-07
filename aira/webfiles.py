"""Where the browser's files live: the shell's in ``auto_apply_assistant/``, each app's next to its own code.

Monosage is one project with one folder per app. An app's folder holds its server code and, in ``web/``, its screens
(``views-<app>.js``), public pages (``public-<app>.js``) and styles (``app-<app>.css``); for example
``aira/suite/shop/web/``. The shell (``app.js``, ``shell.js``, the sign-in, translations, icons, the Careers screens)
stays in ``auto_apply_assistant/``.

The browser still asks for every file as ``/static/<name>``: :func:`find` answers that from one index, so moving a file
between folders never changes an address. File names are unique across all folders (:func:`index` refuses a clash).
"""

from __future__ import annotations

import threading
from pathlib import Path

__all__ = ["SHELL", "app_dirs", "find", "index", "scripts"]

ROOT = Path(__file__).resolve().parent.parent
SHELL = ROOT / "auto_apply_assistant"
_APPS = ROOT / "aira"
_lock = threading.Lock()
_index: dict[str, Path] | None = None


def app_dirs() -> list[Path]:
    """Every app's ``web/`` folder (``aira/suite/<app>/web``, ``aira/leads/web``, ``aira/airaapi/web`` ...)."""
    return sorted(
        path
        for path in _APPS.rglob("web")
        if path.is_dir() and "__pycache__" not in path.parts
    )


def _build() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for folder in [SHELL, *app_dirs()]:
        for path in folder.iterdir():
            if not path.is_file():
                continue
            if path.name in found:
                raise RuntimeError(
                    f"Two browser files are called {path.name}: {found[path.name]} and {path}. Rename one."
                )
            found[path.name] = path
    return found


def index(*, refresh: bool = False) -> dict[str, Path]:
    """File name -> path for every file the browser can load as ``/static/<name>``."""
    global _index
    with _lock:
        if _index is None or refresh:
            _index = _build()
        return _index


def find(name: str) -> Path | None:
    """The file served as ``/static/<name>``, or None. A file added while the server runs is found too."""
    path = index().get(name)
    if path is None or not path.is_file():
        path = index(refresh=True).get(name)
    return path


def scripts() -> list[Path]:
    """Every JavaScript file of the shell and the apps (the translation catalog reads them)."""
    return sorted(
        (path for path in index().values() if path.suffix == ".js"),
        key=lambda path: path.name,
    )

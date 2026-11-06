"""The old name of :mod:`monosage_cloud`, kept so existing commands, deployments and imports keep working.

``import aira_cloud.policy`` is ``monosage_cloud.policy`` (the same module object, so hooks and state are shared),
and ``python -m aira_cloud ...`` runs ``python -m monosage_cloud ...``.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from importlib.machinery import ModuleSpec
from types import ModuleType

import monosage_cloud as _new

_OLD, _NEW = __name__, "monosage_cloud"


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Answers ``aira_cloud.<name>`` with ``monosage_cloud.<name>`` (importing it if needed)."""

    def __init__(self) -> None:
        self._specs: dict[str, ModuleSpec | None] = {}

    def find_spec(self, fullname: str, path: object = None, target: object = None):  # noqa: ANN201
        if not fullname.startswith(f"{_OLD}.") or fullname == f"{_OLD}.__main__":
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec) -> ModuleType:  # noqa: ANN001
        module = importlib.import_module(_NEW + spec.name[len(_OLD) :])
        self._specs[spec.name] = module.__spec__
        return module

    def exec_module(self, module: ModuleType) -> None:
        # importlib gave the module the alias's spec; it keeps its own.
        spec = module.__spec__
        if spec is not None:
            module.__spec__ = self._specs.pop(spec.name, spec)


if not any(isinstance(finder, _Alias) for finder in sys.meta_path):
    sys.meta_path.insert(0, _Alias())

__all__ = getattr(_new, "__all__", [])
for _name in dir(_new):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_new, _name)

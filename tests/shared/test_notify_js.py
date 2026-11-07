"""The live stream's page logic (realtime.js) under plain node, and the notification scripts parse."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    return subprocess.run(
        [NODE, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_realtime_logic() -> None:
    result = _run("tests/js/realtime.test.mjs")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failed" in result.stdout


@pytest.mark.parametrize("name", ["realtime.js", "notify.js", "sw.js"])
def test_the_scripts_parse(name: str) -> None:
    result = _run("--check", f"auto_apply_assistant/{name}")
    assert result.returncode == 0, result.stderr

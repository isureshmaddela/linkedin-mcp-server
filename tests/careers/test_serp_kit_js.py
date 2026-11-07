"""Run the dependency-free node tests for the API UI kit when node is installed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from aira import webfiles

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


def test_serp_kit_logic() -> None:
    result = _run("tests/js/serp_kit.test.mjs")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failed" in result.stdout


def test_serp_kit_parses() -> None:
    result = _run("--check", "auto_apply_assistant/views-serp-kit.js")
    assert result.returncode == 0, result.stderr


def test_playground_logic() -> None:
    result = _run("tests/js/playground.test.mjs")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failed" in result.stdout


def test_playground_parses() -> None:
    result = _run("--check", str(webfiles.find("views-playground.js")))
    assert result.returncode == 0, result.stderr

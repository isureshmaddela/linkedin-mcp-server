"""Monosage Cloud was ``aira_cloud``: the old imports and ``python -m aira_cloud`` keep working, on the same modules."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_old_imports_are_the_same_modules():
    import aira_cloud.policy
    import aira_cloud.routes.org
    from aira_cloud.app import CloudApp

    import monosage_cloud.app
    import monosage_cloud.policy
    import monosage_cloud.routes.org

    assert aira_cloud.policy is monosage_cloud.policy
    assert aira_cloud.routes.org is monosage_cloud.routes.org
    assert CloudApp is monosage_cloud.app.CloudApp
    assert (
        monosage_cloud.policy.__spec__.name == "monosage_cloud.policy"
    )  # it keeps its own name


def test_both_commands_run_the_same_program():
    for name in ("monosage_cloud", "aira_cloud"):
        done = subprocess.run(
            [sys.executable, "-m", name, "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr
        assert "import-local" in done.stdout and "Monosage Cloud" in done.stdout


def test_the_old_start_scripts_forward_to_the_new_ones():
    for old, new in (
        ("start_aira_cloud.ps1", "start_monosage_cloud.ps1"),
        ("start_aira.ps1", "start_monosage.ps1"),
    ):
        text = (ROOT / "scripts" / old).read_text(encoding="utf-8")
        assert new in text and "@PSBoundParameters" in text
    assert "monosage_cloud import-local" in (
        ROOT / "scripts" / "start_monosage_cloud.ps1"
    ).read_text(encoding="utf-8")

"""Public forms send back a ticket from their page and are refused when sent within a few seconds
(``kit.check_form``). Tests that post a public form import ``quick_forms`` (autouse) and add ``started(page)``."""

from __future__ import annotations

from typing import Any

import pytest

from aira.suite import kit


@pytest.fixture(autouse=True)
def quick_forms(monkeypatch):
    """Test-only: no wait before a form may be sent (the wait itself is covered by test_suite_email_delivery)."""
    monkeypatch.setattr(kit, "FORM_MIN_SECONDS", 0)


def started(page: dict[str, Any]) -> str:
    """The ticket a public page hands out (``GET /api/public/<token>``)."""
    return str(page["page"]["started"])

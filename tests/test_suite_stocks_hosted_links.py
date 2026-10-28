"""Stocks, hosted: an analyst's public page works on a real hosted server, whatever order the modules load in.

``aira_cloud serve`` imports the gateway, builds the ``CloudApp`` (which installs the organization's public-link
index) and only then builds the ``Gateway``, whose first import of the local build used to put the single-user
index back. Links made after that answered 404 to every visitor. The other hosted tests import the local build
first, which hides it, so this one runs the server's own order in a fresh interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r"""
import json, sys
from pathlib import Path

from aira_cloud.gateway import Gateway  # what cmd_serve imports first
assert "auto_apply_assistant" not in sys.modules, "the local build must not be imported yet"
from cloud_support import Browser, build_org, make_app, sign_in
from aira.suite import kit, public

app = make_app(Path(sys.argv[1]))
installed = type(public._index).__name__
Gateway(app)  # the first import of the local build happens here, as on the real server
after = type(public._index).__name__
kit.forget_schema()
org = build_org(app)
app.billing.subscribe_app(org.owner, "stocks")
out = {"installed": installed, "after_gateway": after, "roles": {}}
for who in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
    browser = sign_in(app, getattr(org, who))
    browser.call("POST", "/api/stocks/analyst",
                 {"display_name": f"{who} picks", "price": "5", "payment_link": "https://pay.example/x"})
    made_status, made = browser.call("POST", "/api/stocks/analyst/link", {})
    token = made["link"]["token"]
    visitor = Browser(app)
    page_status, page = visitor.call("GET", f"/api/public/{token}")
    sub_status, _ = visitor.call("POST", f"/api/public/{token}/subscribe",
                                 {"name": "Vi", "email": f"vi-{who}@example.com"})
    subscribers = browser.call("GET", "/api/stocks/ideas")[1]["subscribers"]
    out["roles"][who] = [made_status, page_status, (page.get("page") or {}).get("analyst", {}).get("display_name"),
                         sub_status, len(subscribers)]
print("RESULT " + json.dumps(out))
"""


def test_public_analyst_links_work_when_the_server_builds_the_gateway_after_the_app(
    tmp_path,
):
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests")]),
        "AIRA_SUITE_STRICT": "1",
        "AIRA_MARKET_PROVIDER": "stub",
        "AUTO_APPLY_DB_PATH": str(tmp_path / "local" / "applications.sqlite3"),
        "AIRA_ACCOUNT_URL": "",
        "AIRA_ACCOUNT_SECRET": "",
        "AIRA_ACCOUNT_ALLOW_LOCAL": "",
    }
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(tmp_path / "hosted")],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    lines = [x for x in done.stdout.splitlines() if x.startswith("RESULT ")]
    assert done.returncode == 0 and lines, done.stderr[-3000:]
    result = json.loads(lines[-1][len("RESULT ") :])
    assert result["installed"] == "HostedPublicIndex"
    assert result["after_gateway"] == "HostedPublicIndex"
    for who, (made, page, name, subscribed, subscribers) in result["roles"].items():
        assert (made, page, name, subscribed, subscribers) == (
            200,
            200,
            f"{who} picks",
            201,
            1,
        ), who


def test_the_workaround_keeps_a_hosted_index_and_still_changes_the_default(
    monkeypatch,
):
    from aira.suite import public
    from aira.suite.stocks import links
    from aira_cloud.public_links import HostedPublicIndex

    def buggy(
        index,
    ):  # what aira/suite/public.py does today: the default is also made current
        public._default_index = public._index = index

    monkeypatch.setattr(public, "set_default_index", buggy)
    monkeypatch.setattr(public, "_index", public._index)
    monkeypatch.setattr(public, "_default_index", public._default_index)
    links.install()
    assert public.set_default_index is not buggy
    links.install()  # twice is once
    hosted = HostedPublicIndex(object())  # type: ignore[arg-type]
    public.set_index(hosted)
    local = public._MemoryIndex()
    public.set_default_index(local)
    assert public._index is hosted and public._default_index is local
    public.reset_index()
    assert public._index is local
    # without a hosted index the default is made current, as before
    other = public._MemoryIndex()
    public.set_default_index(other)
    assert public._index is other

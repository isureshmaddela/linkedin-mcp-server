"""The page policy: service workers and the web manifest only from this server."""

from __future__ import annotations

from aira.security import SECURITY_HEADERS


def _directives() -> dict[str, str]:
    csp = dict(SECURITY_HEADERS)["Content-Security-Policy"]
    return dict(part.strip().split(" ", 1) for part in csp.split(";") if part.strip())


def test_workers_and_the_manifest_come_from_this_server_only():
    directives = _directives()
    assert directives["worker-src"] == "'self'"
    assert directives["manifest-src"] == "'self'"
    assert directives["script-src"] == "'self'" and directives["object-src"] == "'none'"

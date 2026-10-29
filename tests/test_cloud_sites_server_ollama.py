"""Sites in Monosage Cloud builds with the server's own Ollama ("Ollama on the Monosage server").

When the operator runs the platform model on Ollama (``AIRA_CLOUD_MODEL_PROVIDER=ollama``), organizations may build
sites with the models installed there, streamed like the single-user build's Ollama. The address is the operator's
setting: a member cannot point it elsewhere, and their own addresses must still be public https ones.
"""

from __future__ import annotations

import pytest
from cloud_support import build_org, make_app, sign_in
from sites_stubs import SitesStub

from aira.hosting import HOOKS
from aira.suite import kit
from aira.suite.sites import providers


@pytest.fixture
def stub():
    server = SitesStub().start()
    yield server
    server.stop()


def _cloud(tmp_path, stub, **overrides):
    HOOKS.reset()
    return make_app(
        tmp_path, **{"model_provider": "ollama", "model_url": stub.base, **overrides}
    )


def test_an_organization_builds_a_site_with_the_servers_ollama(tmp_path, stub):
    cloud = _cloud(tmp_path, stub)
    try:
        kit.forget_schema()
        org = build_org(cloud, apps="all")
        owner = sign_in(cloud, org.owner)
        status, overview = owner.call("GET", "/api/sites/overview")
        assert status == 200, overview
        listed = {p["id"]: p for p in overview["providers"]["providers"]}
        assert (
            "server" in listed and "ollama" not in listed
        )  # never "this computer" online
        server = listed["server"]
        assert server["fixed_address"] is True and server["base_url"] == stub.base
        assert (
            overview["default_provider"] == "server"
        )  # it streams; the platform model answers in one piece

        # The address cannot be changed by a member: what they send is ignored.
        status, saved = owner.call(
            "POST",
            "/api/sites/providers/server",
            {"base_url": "http://169.254.169.254", "model": "stub-coder:7b"},
        )
        assert status == 200, saved
        status, overview = owner.call("GET", "/api/sites/overview")
        again = {p["id"]: p for p in overview["providers"]["providers"]}["server"]
        assert again["base_url"] == stub.base and again["model"] == "stub-coder:7b"

        status, made = owner.call(
            "POST", "/api/sites/projects", {"prompt": "A bakery website"}
        )
        assert status == 201, made
        project = made["project"]
        status, built = owner.call(
            "POST",
            f"/api/sites/projects/{project['id']}/generate",
            {"prompt": "A bakery website", "provider": "server"},
        )
        assert status == 200, built
        assert built["run"]["status"] == "done" and built["version"] == 1
        chats = [r for r in stub.requests if r["path"] == "/api/chat"]
        assert (
            chats
            and chats[-1]["json"]["model"] == "stub-coder:7b"
            and chats[-1]["json"]["stream"] is True
        )
    finally:
        cloud.close()


def test_without_ollama_as_the_platform_model_it_is_not_offered(tmp_path, stub):
    cloud = _cloud(tmp_path, stub, model_provider="anthropic")
    try:
        kit.forget_schema()
        org = build_org(cloud, apps="all")
        owner = sign_in(cloud, org.owner)
        overview = owner.call("GET", "/api/sites/overview")[1]
        ids = [p["id"] for p in overview["providers"]["providers"]]
        assert "server" not in ids and overview["default_provider"] == "platform"
        status, data = owner.call(
            "POST",
            f"/api/sites/projects/{owner.call('POST', '/api/sites/projects', {'prompt': 'x site'})[1]['project']['id']}/generate",
            {"prompt": "x site", "provider": "server"},
        )
        assert status == 400
    finally:
        cloud.close()


def test_a_members_own_address_must_still_be_public(tmp_path, stub):
    cloud = _cloud(tmp_path, stub)
    try:
        with pytest.raises(ValueError, match="https"):
            providers.check_base_url(
                providers.PROVIDERS["custom"], "http://127.0.0.1:11434/v1"
            )
        with pytest.raises(providers.ModelError, match="not a public address"):
            providers._check_public_host("https://127.0.0.1/v1/chat/completions")
        providers._check_public_host(
            f"{stub.base}/api/chat"
        )  # the server's own Ollama passes
    finally:
        cloud.close()


def test_a_listed_model_ollama_cannot_open_is_left_out(monkeypatch):
    monkeypatch.setattr(
        providers, "ollama_model_opens", lambda base, name: name != "broken:7b"
    )
    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout=0):
        calls.append(url)
        return Response(b'{"models": [{"name": "broken:7b"}, {"name": "qwen3.5:4b"}]}')

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    assert providers.installed_ollama_model("http://127.0.0.1:11434") == "qwen3.5:4b"

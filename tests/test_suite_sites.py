"""Sites (aira/suite/sites): the builder's parts on their own, the single-user server end to end against stand-ins for
every model API and deploy target, and the hosted build's gating, roles and isolation."""

from __future__ import annotations

import base64
import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household
from sites_stubs import EDIT_ANSWER, SITE_ANSWER, SitesStub

import test_careers
from aira.suite import kit
from aira.suite.sites import deploy, designer, generator, providers, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
)


@pytest.fixture
def services(monkeypatch):
    server = SitesStub().start()
    monkeypatch.setenv("AIRA_GITHUB_API_URL", f"{server.base}/gh")
    monkeypatch.setenv("AIRA_NETLIFY_API_URL", f"{server.base}/netlify")
    monkeypatch.setenv("AIRA_VERCEL_API_URL", f"{server.base}/vercel")
    yield server
    server.stop()


def _get(env, client, path):
    """A raw GET with the signed-in browser's cookies: (status, headers, bytes)."""
    request = urllib.request.Request(env.base + path, method="GET")
    try:
        with client.opener.open(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _anon(env, path):
    request = urllib.request.Request(env.base + path, method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


# ---------------------------------------------------------------------------
# the parts
# ---------------------------------------------------------------------------


def test_file_paths_stay_inside_the_site():
    assert store.clean_path("./css//site.css") == "css/site.css"
    assert store.clean_path("about.html") == "about.html"
    for bad in (
        "../secrets.html",
        "a/../../b.html",
        ".env",
        "img.png",
        "",
        "x" * 200 + ".html",
        "a b.html",
    ):
        with pytest.raises(ValueError):
            store.clean_path(bad)


def test_the_answer_becomes_files_in_every_shape_a_model_writes():
    result = generator.parse(SITE_ANSWER)
    assert result.format == "tags" and sorted(result.files) == [
        "app.js",
        "index.html",
        "styles.css",
    ]
    assert result.plan.startswith(
        "A bright one-page site"
    ) and result.summary.startswith("Built Stub Bakery")
    edit = generator.parse(EDIT_ANSWER)
    assert edit.deleted == ["app.js"] and "about.html" in edit.files
    fenced = generator.parse(
        "Sure!\n```html\n<!DOCTYPE html><html><body>Hi</body></html>\n```\n```css styles.css\nbody{}\n```"
    )
    assert fenced.format == "fences" and sorted(fenced.files) == [
        "index.html",
        "styles.css",
    ]
    bare = generator.parse(
        "Here it is: <!DOCTYPE html><html><body>Hi</body></html> enjoy"
    )
    assert bare.format == "document" and bare.files["index.html"].endswith("</html>\n")
    cut = generator.parse(
        '<file path="index.html">\n<html><body>long</body></html>\n<!-- and then the model stopped'
    )
    assert "index.html" in cut.files
    unsafe = generator.parse(
        '<file path="../../evil.html">x</file><file path="ok.html">y</file>'
    )
    assert list(unsafe.files) == ["ok.html"] and unsafe.skipped == ["../../evil.html"]
    wrapped = generator.parse('<file path="a.css">\n```css\nbody{}\n```\n</file>')
    assert wrapped.files["a.css"] == "body{}\n"
    assert generator.parse("I cannot help with that.").format == "none"
    # A page's own <summary> (a FAQ) is not the model's summary, in the finished answer and while streaming.
    faq = '<plan>FAQ</plan><file path="index.html"><details><summary>How do I start?</summary></details></file><summary>Added a FAQ.</summary>'
    assert generator.parse(faq).summary == "Added a FAQ."
    reader = generator.Reader()
    reader.feed(faq[:-30] + "</file>")
    assert reader.phase == "writing"


def test_the_reader_follows_the_stream():
    reader = generator.Reader()
    reader.feed("<plan>Plan</plan>")
    assert reader.phase == "planning" and reader.plan == "Plan"
    reader.feed('<file path="index.html">\n<h1>')
    assert (
        reader.phase == "writing"
        and reader.current == "index.html"
        and reader.done == []
    )
    reader.feed('</h1>\n</file><file path="a.css">')
    assert reader.done == ["index.html"] and reader.current == "a.css"
    reader.feed("x</file><summary>Done")
    assert reader.phase == "finishing" and reader.done == ["index.html", "a.css"]


def test_the_designer_reads_the_request_and_applies_changes():
    spec = designer.spec_from_prompt(
        "A warm website for Bella Cucina, an Italian restaurant with a menu and reviews"
    )
    assert spec["kind"] == "restaurant" and spec["name"] == "Bella Cucina"
    assert {"menu", "testimonials"} <= set(spec["sections"])
    assert (
        designer.spec_from_prompt("website for a dentist named Smile Studio")["kind"]
        == "clinic"
    )
    assert (
        designer.spec_from_prompt('a landing page for my startup called "Orbit AI"')[
            "name"
        ]
        == "Orbit AI"
    )
    changed, notes = designer.change(
        spec,
        "make it dark, use teal, add a FAQ and remove the menu. headline: Pasta like Nonna's",
    )
    assert changed["dark"] and changed["primary"] == designer.COLORS["teal"][0]
    assert "faq" in changed["sections"] and "menu" not in changed["sections"]
    assert changed["headline"] == "Pasta like Nonna's" and len(notes) == 5
    assert designer.change(spec, "make it amazing")[1] == []
    files = designer.render(changed)
    assert sorted(files) == ["index.html", "script.js", "styles.css"]
    page = files["index.html"]
    assert (
        'id="faq"' in page
        and 'id="menu"' not in page
        and "theme-dark" in page
        and "Pasta like Nonna&#x27;s" in page
    )
    assert (
        '<script src="script.js">' in page and "<script>" not in page
    )  # no inline script
    hostile = designer.render({**changed, "name": "<script>alert(1)</script>"})[
        "index.html"
    ]
    assert "<script>alert(1)" not in hostile


def test_the_deploy_kit_never_replaces_the_sites_own_files():
    files = deploy.bundle(
        {"index.html": "<h1>Hi</h1>", "README.md": "mine"},
        [("logo.png", PNG)],
        name="My Site!",
    )
    assert files["README.md"] == b"mine" and files["assets/logo.png"] == PNG
    for path in (
        "Dockerfile",
        "nginx.conf",
        "netlify.toml",
        "vercel.json",
        "wrangler.toml",
        "firebase.json",
        "staticwebapp.config.json",
        "amplify.yml",
        "render.yaml",
        ".github/workflows/pages.yml",
        ".nojekyll",
    ):
        assert path in files, path
    assert (
        b"listen 8080" in files["nginx.conf"]
        and b'name = "my-site"' in files["wrangler.toml"]
    )
    assert ".github/workflows/pages.yml" not in deploy.bundle(
        {"index.html": "x"}, [], name="s", target="github"
    )
    archive = zipfile.ZipFile(io.BytesIO(deploy.zip_bytes(files)))
    assert (
        archive.read("index.html") == b"<h1>Hi</h1>"
        and "Dockerfile" in archive.namelist()
    )
    assert deploy.check_repo("https://github.com/octo/site.git") == "octo/site"
    with pytest.raises(ValueError):
        deploy.check_repo("bad repo name")


@pytest.mark.parametrize(
    ("provider_id", "base"),
    [
        ("ollama", ""),
        ("custom", "/v1"),
        ("claude", ""),
        ("gemini", "/v1beta"),
        ("openai", "/v1"),
    ],
)
def test_every_kind_of_model_api_streams_the_site(services, provider_id, base):
    provider = providers.PROVIDERS[provider_id]
    config = providers.Config(
        provider, services.base + base, "stub-model", "sk-test-key-1234"
    )
    pieces: list[str] = []
    text = providers.stream(config, "system", "build", pieces.append)
    assert text == SITE_ANSWER and len(pieces) > 5
    call = services.model_calls()[-1]
    sent = {k.lower(): v for k, v in call["headers"].items()}
    if provider_id == "claude":
        assert (
            sent["x-api-key"] == "sk-test-key-1234"
            and sent["anthropic-version"] == "2023-06-01"
        )
        assert call["json"]["stream"] is True and call["json"]["max_tokens"] > 1000
    elif provider_id == "gemini":
        assert (
            sent["x-goog-api-key"] == "sk-test-key-1234"
            and "stub-model:streamGenerateContent" in call["path"]
        )
    elif provider_id == "ollama":
        assert (
            call["json"]["stream"] is True
            and call["json"]["options"]["num_ctx"] >= 8192
        )
    else:
        assert sent["authorization"] == "Bearer sk-test-key-1234"
    assert providers.list_models(config)


def test_model_failures_are_explained_without_the_key(services):
    config = providers.Config(
        providers.PROVIDERS["openai"],
        f"{services.base}/v1",
        "gpt",
        "sk-secret-value-9999",
    )
    services.fail_status = 401
    with pytest.raises(providers.ModelError) as refused:
        providers.stream(config, "s", "p", lambda _p: None)
    assert "refused the key" in str(refused.value) and "sk-secret" not in str(
        refused.value
    )
    services.fail_status = 404
    with pytest.raises(providers.ModelError, match="no model called gpt"):
        providers.stream(config, "s", "p", lambda _p: None)
    services.fail_status = 0
    missing = providers.Config(
        providers.PROVIDERS["claude"], services.base, "claude-sonnet-5-5", None
    )
    with pytest.raises(providers.ModelError, match="Add your Claude"):
        providers.stream(missing, "s", "p", lambda _p: None)
    down = providers.Config(providers.PROVIDERS["ollama"], "http://127.0.0.1:9", "m")
    with pytest.raises(providers.ModelError, match="Is it running"):
        providers.stream(down, "s", "p", lambda _p: None)
    stop_after = iter(range(3))
    with pytest.raises(providers.Stopped):
        providers.stream(
            providers.Config(providers.PROVIDERS["ollama"], services.base, "m"),
            "s",
            "p",
            lambda _p: None,
            lambda: next(stop_after, None) is None,
        )


def test_the_hosted_build_offers_only_reachable_providers(monkeypatch):
    monkeypatch.setattr(providers, "hosted", lambda: True)
    ids = [p.id for p in providers.offered()]
    assert "platform" in ids and "ollama" not in ids and "lmstudio" not in ids
    with pytest.raises(ValueError, match="https"):
        providers.check_base_url(providers.PROVIDERS["custom"], "http://10.0.0.5/v1")
    monkeypatch.delenv(providers.ALLOW_LOCAL_ENV, raising=False)
    with pytest.raises(providers.ModelError, match="not a public address"):
        providers._check_public_host("https://localhost/v1")
    with pytest.raises(ValueError):
        providers.get("ollama")


# ---------------------------------------------------------------------------
# the single-user server, end to end
# ---------------------------------------------------------------------------


def _new(api_, **body):
    return api_.post("/api/sites/projects", body, expect=201)["project"]


def test_build_preview_change_restore_and_publish_on_the_local_server(
    env, api, services
):
    kit.forget_schema()
    overview = api.get("/api/sites/overview")
    assert (
        overview["default_provider"] == "builtin"
        and overview["sites"] == 0
        and len(overview["templates"]) == 12
    )
    ids = [p["id"] for p in overview["providers"]["providers"]]
    assert ids[:3] == ["builtin", "ollama", "lmstudio"] and "platform" not in ids

    # 1. The offline designer builds the first version.
    project = _new(
        api, prompt="A website for Pulse Studio gym with classes and pricing"
    )
    assert project["name"] == "Pulse Studio" and project["links"]["preview"][
        "url"
    ].endswith("/w/")
    built = api.post(
        f"/api/sites/projects/{project['id']}/generate",
        {
            "prompt": "A website for Pulse Studio gym with classes and pricing",
            "provider": "builtin",
        },
    )
    assert built["run"]["status"] == "done" and built["version"] == 1
    assert sorted(built["project"]["files"]) == [
        "index.html",
        "script.js",
        "styles.css",
    ]
    assert built["message"]["meta"]["changed"] == [
        "index.html",
        "script.js",
        "styles.css",
    ]
    preview = built["project"]["links"]["preview"]["url"]

    # The preview runs the site in a sandbox: its own page policy, the app's strict one replaced.
    status, headers, body = _anon(env, preview)
    assert status == 200 and b"Pulse Studio" in body
    policy = headers["Content-Security-Policy"]
    assert (
        policy.startswith("sandbox allow-scripts") and "allow-same-origin" not in policy
    )
    assert (
        headers["X-Frame-Options"] == "SAMEORIGIN"
        and headers["Cross-Origin-Resource-Policy"] == "cross-origin"
    )
    assert "X-Content-Type-Options" in headers and "Referrer-Policy" in headers
    assert _anon(env, preview + "styles.css")[1]["Content-Type"].startswith("text/css")
    assert _anon(env, preview + "missing.html")[0] == 404
    assert _anon(env, preview[:-1])[0] == 301
    assert (
        _anon(
            env,
            project["links"]["preview"]["path"]
            .replace("/p/", "/api/public/")
            .rstrip("/")
            + "x" * 3
            + "/w/",
        )[0]
        == 404
    )

    # 2. The designer applies a change it understands, and says so when it cannot.
    dark = api.post(
        f"/api/sites/projects/{project['id']}/generate",
        {"prompt": "make it light and use green"},
    )
    assert (
        dark["version"] == 2
        and "Switched to a light theme." in dark["message"]["content"]
    )  # the gym look starts dark
    assert (
        "theme-light" in dark["project"]["files"]["index.html"]
        and "#16a34a" in dark["project"]["files"]["styles.css"]
    )
    roles = [m["role"] for m in dark["project"]["messages"]]
    assert roles == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]  # a request and its same-second answer stay in order
    vague = api.post(
        f"/api/sites/projects/{project['id']}/generate",
        {"prompt": "make it more exciting"},
    )
    assert vague["version"] is None and "without a model" in vague["message"]["content"]

    # 3. A model (any OpenAI-compatible server) changes it: changed files, a new page, a deletion.
    status = api.post(
        "/api/sites/providers/custom",
        {
            "base_url": f"{services.base}/v1",
            "model": "stub-model-a",
            "api_key": "sk-custom-key-5678",
            "default": True,
        },
    )
    custom = next(p for p in status["providers"] if p["id"] == "custom")
    assert (
        custom["configured"]
        and custom["last4"] == "5678"
        and custom["ready"]
        and status["default"] == "custom"
    )
    assert "sk-custom-key-5678" not in json.dumps(status)
    services.answer = SITE_ANSWER
    replaced = api.post(
        f"/api/sites/projects/{project['id']}/generate",
        {
            "prompt": "Rebuild it as Stub Bakery",
            "provider": "custom",
            "run_id": "ab" * 16,
        },
    )
    assert (
        replaced["version"] == 3 and replaced["project"]["designer"] is False
    )  # a model rewrote it
    call = services.model_calls()[-1]
    assert (
        call["json"]["model"] == "stub-model-a"
        and call["headers"]["Authorization"] == "Bearer sk-custom-key-5678"
    )
    assert "Rebuild it as Stub Bakery" in call["json"]["messages"][1]["content"]
    assert (
        '<file path="index.html">' in call["json"]["messages"][1]["content"]
    )  # the current site went along
    run = api.get(f"/api/sites/runs/{'ab' * 16}?from=0")["run"]
    assert (
        run["status"] == "done"
        and run["output"] == SITE_ANSWER
        and run["files"] == ["index.html", "styles.css", "app.js"]
    )
    assert (
        api.get(f"/api/sites/runs/{'ab' * 16}?from=100")["run"]["output"]
        == SITE_ANSWER[100:]
    )
    # A root link (href="/styles.css") is served relative to the site, so it never leaves the site's folder.
    assert b'href="styles.css"' in _anon(env, preview)[2]
    services.answer = EDIT_ANSWER
    edited = api.post(
        f"/api/sites/projects/{project['id']}/generate",
        {"prompt": "Green heading and an about page"},
    )
    files = edited["project"]["files"]
    assert (
        edited["version"] == 4
        and "about.html" in files
        and "app.js" not in files
        and "rgb(20, 160, 60)" in files["styles.css"]
    )
    assert sorted(edited["message"]["meta"]["changed"]) == [
        "about.html",
        "app.js",
        "styles.css",
    ]
    assert _anon(env, preview + "about")[0] == 200  # a page without .html

    # 4. Hand edits make versions (typing on within two minutes keeps one), restore goes back.
    saved = api.ok(
        "PUT",
        f"/api/sites/projects/{project['id']}/files",
        {"path": "styles.css", "content": "h1{color:red}"},
    )
    again = api.ok(
        "PUT",
        f"/api/sites/projects/{project['id']}/files",
        {"path": "styles.css", "content": "h1{color:blue}"},
    )
    assert saved["version"] == again["version"] == 5
    assert (
        api.call(
            "PUT",
            f"/api/sites/projects/{project['id']}/files",
            {"path": "../x.html", "content": "x"},
        )[0]
        == 400
    )
    restored = api.post(f"/api/sites/projects/{project['id']}/versions/3/restore")
    assert (
        restored["version"] == 6
        and "app.js" in restored["project"]["files"]
        and "about.html" not in restored["project"]["files"]
    )
    assert [v["number"] for v in restored["project"]["versions"]] == [6, 5, 4, 3, 2, 1]
    version_two = _anon(env, preview.replace("/w/", "/v/2/"))
    assert version_two[0] == 200 and b"theme-light" in version_two[2]

    # 5. Publishing serves a fixed version; later edits stay in the preview until published again.
    published = api.post(f"/api/sites/projects/{project['id']}/publish")
    site = published["link"]["url"]
    assert published["version"] == 6 and site != preview
    api.ok(
        "PUT",
        f"/api/sites/projects/{project['id']}/files",
        {"path": "index.html", "content": "<h1>Draft</h1>"},
    )
    live = _anon(env, site)
    assert (
        b"Stub Bakery" in live[2]
        and live[1]["Cache-Control"].startswith("public")
        and "X-Frame-Options" not in live[1]
    )
    assert b"Draft" in _anon(env, preview)[2]
    assert (
        _anon(env, site.replace("/w/", "/v/1/"))[0] == 404
    )  # versions are the preview link's, not the public's
    short = api.get(published["link"]["path"].replace("/p/", "/api/public/"))
    assert short["page"]["url"] == site
    api.ok("DELETE", f"/api/sites/projects/{project['id']}/publish")
    assert _anon(env, site)[0] == 404

    # 6. Search, the list, CSV, duplicate, delete.
    assert api.get("/api/sites/search?q=Pulse")["projects"][0]["id"] == project["id"]
    copy = api.post(f"/api/sites/projects/{project['id']}/duplicate", expect=201)[
        "project"
    ]
    assert (
        copy["name"] == "Pulse Studio (copy)"
        and copy["files"]["index.html"] == "<h1>Draft</h1>\n"
        or copy["files"]
    )
    status, _headers, csv = _get(env, api, "/api/sites/projects.csv")
    assert status == 200 and b"Pulse Studio (copy)" in csv
    api.ok("DELETE", f"/api/sites/projects/{copy['id']}")
    assert api.call("GET", f"/api/sites/projects/{copy['id']}")[0] == 404
    assert _anon(env, copy["links"]["preview"]["url"])[0] == 404


def test_templates_images_export_and_client_payment(env, api, services):
    kit.forget_schema()
    project = _new(api, template="saas")
    assert (
        project["name"] == "Nimbus" and project["version"] == 1 and project["designer"]
    )
    added = api.post(
        f"/api/sites/projects/{project['id']}/assets",
        {"name": "Logo Mark.PNG", "data": base64.b64encode(PNG).decode()},
        expect=201,
    )
    assert added["asset"]["path"] == "assets/logo-mark.png"
    status, headers, body = _anon(
        env, project["links"]["preview"]["url"] + "assets/logo-mark.png"
    )
    assert status == 200 and body == PNG and headers["Content-Type"] == "image/png"
    bad_svg = base64.b64encode(
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    ).decode()
    assert (
        api.call(
            "POST",
            f"/api/sites/projects/{project['id']}/assets",
            {"name": "x.svg", "data": bad_svg},
        )[0]
        == 400
    )
    assert (
        api.call(
            "POST",
            f"/api/sites/projects/{project['id']}/assets",
            {"name": "x.png", "data": base64.b64encode(b"not an image").decode()},
        )[0]
        == 400
    )

    # A model is told about uploaded images.
    api.post(
        "/api/sites/providers/ollama",
        {"base_url": services.base, "model": "stub-coder:7b"},
    )
    api.post(
        f"/api/sites/projects/{project['id']}/generate",
        {"prompt": "Use my logo", "provider": "ollama"},
    )
    assert (
        "assets/logo-mark.png (image/png)"
        in services.model_calls()[-1]["json"]["messages"][1]["content"]
    )
    assert api.get("/api/sites/providers/ollama/models")["models"] == [
        "llama-stub:1b",
        "stub-coder:7b",
    ]

    status, headers, body = _get(
        env, api, f"/api/sites/projects/{project['id']}/export.zip"
    )
    archive = zipfile.ZipFile(io.BytesIO(body))
    names = set(archive.namelist())
    assert (
        status == 200
        and headers["Content-Type"] == "application/zip"
        and 'filename="nimbus.zip"' in headers["Content-Disposition"]
    )
    assert {
        "index.html",
        "assets/logo-mark.png",
        "Dockerfile",
        "netlify.toml",
        "README.md",
        ".github/workflows/pages.yml",
    } <= names
    plain = zipfile.ZipFile(
        io.BytesIO(
            _get(env, api, f"/api/sites/projects/{project['id']}/export.zip?kit=0")[2]
        )
    ).namelist()
    assert "Dockerfile" not in plain and "index.html" in plain
    first = zipfile.ZipFile(
        io.BytesIO(
            _get(env, api, f"/api/sites/projects/{project['id']}/export.zip?version=1")[
                2
            ]
        )
    )
    assert b"Nimbus" in first.read("index.html")

    assert (
        api.call("POST", f"/api/sites/projects/{project['id']}/paid", {"paid": True})[0]
        == 400
    )  # no price yet
    api.ok(
        "PATCH",
        f"/api/sites/projects/{project['id']}",
        {"client": "Acme Ltd", "price_cents": 120000},
    )
    paid = api.post(f"/api/sites/projects/{project['id']}/paid", {"paid": True})[
        "project"
    ]
    assert paid["paid"]
    revenue = api.get("/api/suite/revenue?app=sites")
    assert [(line["amount_cents"], line["customer"]) for line in revenue["lines"]] == [
        (120000, "Acme Ltd")
    ]
    hub = api.get("/api/suite/home")
    assert "sites" in json.dumps(hub)
    api.post(f"/api/sites/projects/{project['id']}/paid", {"paid": False})
    assert api.get("/api/sites/overview")["paid_cents"] == 0


def test_github_netlify_and_vercel_deploys(env, api, services):
    kit.forget_schema()
    project = _new(api, template="restaurant")
    assert (
        api.call(
            "POST", f"/api/sites/projects/{project['id']}/deploy", {"target": "github"}
        )[0]
        == 400
    )  # no token
    assert (
        api.call(
            "POST", "/api/sites/deploy-targets/github", {"token": "ghp_wrongtoken0000"}
        )[0]
        == 400
    )
    saved = api.post("/api/sites/deploy-targets/github", {"token": "ghp_stubtoken1234"})
    assert saved["account"] == "octo" and saved["last4"] == "1234"
    targets = api.get("/api/sites/deploy-targets")["targets"]
    assert targets[0]["connected"] and "ghp_stub" not in json.dumps(targets)

    pushed = api.post(
        f"/api/sites/projects/{project['id']}/deploy",
        {"target": "github", "repo": "bella-site", "pages": True},
    )
    result = pushed["deploy"]["result"]
    assert (
        result["repo"] == "octo/bella-site"
        and result["created"]
        and result["pages_url"] == "https://octo.github.io/bella-site/"
    )
    repo = services.repos["octo/bella-site"]
    assert {"index.html", "styles.css", "script.js", "Dockerfile", "README.md"} <= set(
        repo["files"]
    )
    assert (
        ".github/workflows/pages.yml" not in repo["files"]
    )  # pushing workflows needs a wider token
    assert b"Bella Cucina" in repo["files"]["index.html"]
    api.ok(
        "PUT",
        f"/api/sites/projects/{project['id']}/files",
        {"path": "index.html", "content": "<h1>v2</h1>"},
    )
    api.post(
        f"/api/sites/projects/{project['id']}/deploy", {"target": "github"}
    )  # the saved repo is reused
    assert repo["files"]["index.html"] == b"<h1>v2</h1>" and len(repo["commits"]) == 3

    api.post("/api/sites/deploy-targets/netlify", {"token": "nfp_stubtoken1234"})
    first = api.post(
        f"/api/sites/projects/{project['id']}/deploy", {"target": "netlify"}
    )
    second = api.post(
        f"/api/sites/projects/{project['id']}/deploy", {"target": "netlify"}
    )
    assert (
        first["deploy"]["url"] == second["deploy"]["url"] == "https://site1.netlify.app"
    )
    assert (
        len(services.netlify_sites) == 1
        and "index.html" in services.netlify_deploys[-1]["files"]
    )
    assert services.netlify_deploys[-1]["type"] == "application/zip"

    api.post("/api/sites/deploy-targets/vercel", {"token": "vc_wrongtoken0000"})
    status, failed = api.call(
        "POST", f"/api/sites/projects/{project['id']}/deploy", {"target": "vercel"}
    )
    assert (
        status == 502
        and "token" in failed["error"]
        and "vc_wrong" not in failed["error"]
    )
    api.post("/api/sites/deploy-targets/vercel", {"token": "vc_stubtoken1234"})
    done = api.post(f"/api/sites/projects/{project['id']}/deploy", {"target": "vercel"})
    assert (
        done["deploy"]["url"] == "https://bella-cucina.vercel.app"
        and services.vercel_deploys[-1]["target"] == "production"
    )
    history = api.get(f"/api/sites/projects/{project['id']}")["project"][
        "deploy_history"
    ]
    assert [d["status"] for d in history[:2]] == ["done", "failed"]
    api.ok("DELETE", "/api/sites/deploy-targets/vercel")
    assert not api.get("/api/sites/deploy-targets")["targets"][2]["connected"]


def test_a_run_can_be_stopped_and_failures_keep_the_site(env, api, services):
    kit.forget_schema()
    project = _new(api, prompt="Stub Bakery")
    api.post(
        "/api/sites/providers/ollama",
        {"base_url": services.base, "model": "stub-coder:7b"},
    )
    services.delay = 0.05
    services.chunk = 10
    run_id = "cd" * 16
    outcome = {}
    thread = threading.Thread(
        target=lambda: outcome.update(
            api.post(
                f"/api/sites/projects/{project['id']}/generate",
                {"prompt": "slow", "provider": "ollama", "run_id": run_id},
            )
        )
    )
    thread.start()
    for _ in range(100):
        status, body = api.call("GET", f"/api/sites/runs/{run_id}")
        if status == 200 and body["run"]["length"] > 0:
            break
        time.sleep(0.05)
    assert body["run"]["status"] == "running"
    assert (
        api.call(
            "POST",
            f"/api/sites/projects/{project['id']}/generate",
            {"prompt": "second"},
        )[0]
        == 400
    )  # one at a time
    assert api.post(f"/api/sites/runs/{run_id}/cancel")["run"]["status"] == "running"
    thread.join(30)
    assert outcome["run"]["status"] == "cancelled" and outcome["version"] is None
    assert outcome["message"]["content"].startswith("Stopped")
    services.delay = 0
    services.chunk = 40

    services.fail_status = 500
    failed = api.post(
        f"/api/sites/projects/{project['id']}/generate",
        {"prompt": "again", "provider": "ollama"},
    )
    assert (
        failed["run"]["status"] == "failed"
        and "problem on its side" in failed["message"]["content"]
    )
    services.fail_status = 0
    services.answer = "I would love to help, but here is no code."
    empty = api.post(
        f"/api/sites/projects/{project['id']}/generate",
        {"prompt": "again", "provider": "ollama"},
    )
    assert (
        empty["run"]["status"] == "failed"
        and "did not write any files" in empty["message"]["content"]
    )
    assert api.get(f"/api/sites/projects/{project['id']}")["project"]["files"] == {}
    assert (
        api.call(
            "POST",
            f"/api/sites/projects/{project['id']}/generate",
            {"prompt": "x", "provider": "claude"},
        )[0]
        == 400
    )  # no key


def test_a_childs_profile_cannot_open_sites(env, api):
    kit.forget_schema()
    home = Household(env, api)
    mia = home.add("Mia", age_group="child")
    assert mia.call("GET", "/api/sites/overview")[0] == 403
    assert home.owner.get("/api/sites/overview")["sites"] == 0


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_sites_need_the_app_respect_roles_and_stay_in_their_organization(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("GET", "/api/sites/overview")
    assert status == 402 and body["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "sites")
    status, overview = owner.call("GET", "/api/sites/overview")
    assert status == 200, overview
    ids = [p["id"] for p in overview["providers"]["providers"]]
    assert (
        "platform" in ids
        and "ollama" not in ids
        and overview["default_provider"] == "platform"
    )

    status, made = owner.call("POST", "/api/sites/projects", {"template": "portfolio"})
    assert status == 201, made
    project = made["project"]
    for who in ("admin", "coach"):
        assert (
            sign_in(cloud, getattr(org, who)).call(
                "GET", f"/api/sites/projects/{project['id']}"
            )[0]
            == 200
        ), who
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/sites/projects")[0] == 200
    assert auditor.call("POST", "/api/sites/projects", {"prompt": "x"})[0] == 403
    assert sign_in(cloud, org.candidate).call("GET", "/api/sites/projects")[0] == 403
    assert (
        owner.call(
            "POST", "/api/sites/providers/custom", {"base_url": "http://10.1.2.3/v1"}
        )[0]
        == 400
    )

    visitor = Browser(cloud)
    status, page = visitor.call("GET", project["links"]["preview"]["url"], raw=True)
    assert status == 200 and b"Ava Chen" in page
    assert visitor.header("Content-Security-Policy").startswith("sandbox allow-scripts")
    assert visitor.header("X-Frame-Options") == "SAMEORIGIN"
    status, published = owner.call(
        "POST", f"/api/sites/projects/{project['id']}/publish", {}
    )
    assert status == 200, published
    assert visitor.call("GET", published["link"]["url"], raw=True)[0] == 200

    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "sites")
    stranger = sign_in(cloud, other.owner)
    assert stranger.call("GET", "/api/sites/projects")[1]["projects"] == []
    assert stranger.call("GET", f"/api/sites/projects/{project['id']}")[0] == 404


def test_only_a_script_sandbox_without_the_apps_origin_replaces_the_app_policy():
    from aira.airaapi import http as airaapi_http
    from aira.suite.sites import service

    assert airaapi_http.sandboxed(
        service.site_headers("http://127.0.0.1:8765", published=False)
    )
    assert not airaapi_http.sandboxed(
        airaapi_http.UNTRUSTED_PAGE_HEADERS
    )  # saved pages keep the app policy too
    assert not airaapi_http.sandboxed(
        (("Content-Security-Policy", "sandbox allow-scripts allow-same-origin"),)
    )
    assert not airaapi_http.sandboxed((("X-Frame-Options", "SAMEORIGIN"),))

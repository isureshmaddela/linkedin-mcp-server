"""The /legal pages, and the documentation set for the hosted build: links resolve, templates are marked,
the API reference matches the routes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from cloud_support import Browser  # noqa: F401

import aira_cloud.routes  # noqa: F401  (registers the control-plane routes)
from aira_cloud.routes import legal
from aira_cloud.webio import CLOUD_ROUTES

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
LEGAL_FILES = sorted((DOCS / "legal").glob("*.md"))
HOSTED_DOCS = [
    DOCS / name
    for name in (
        "multitenant.md",
        "multitenant-operations.md",
        "multitenant-security.md",
        "pricing.md",
        "hosted-api.md",
    )
] + LEGAL_FILES


@pytest.fixture
def web(cloud):  # noqa: F811
    return Browser(cloud)


# ----------------------------------------------------------------------------- the route


@pytest.mark.parametrize("name", sorted(legal.PAGES))
def test_each_legal_page_is_served_as_html(web, name):
    status, body = web.call("GET", f"/legal/{name}", raw=True)
    text = body.decode("utf-8")
    assert status == 200
    assert "text/html" in web.header("Content-Type")
    assert "TEMPLATE" in text, "every legal page says it is a template"
    assert "<script" not in text.lower()
    assert legal.PAGES[name] in text


def test_index_lists_every_page_and_needs_no_sign_in(web):
    status, body = web.call("GET", "/legal", raw=True)
    text = body.decode("utf-8")
    assert status == 200
    for name in legal.PAGES:
        assert f'href="/legal/{name}"' in text


@pytest.mark.parametrize(
    "path",
    [
        "/legal/nope",
        "/legal/terms.md",
        "/legal/../README",
        "/legal/%2e%2e%2fREADME",
        "/legal/Terms",
        "/legal/terms/extra",
        "/legal//etc/passwd",
    ],
)
def test_only_whitelisted_names_are_served(web, path):
    status, body = web.call("GET", path, raw=True)
    assert status == 404
    assert b"Terms of Service" not in body


def test_other_methods_are_not_routes(web):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, _ = web.call(method, "/legal/terms", {})
        assert status == 404


def test_pages_set_the_security_headers_and_the_request_id(web):
    web.call("GET", "/legal/terms", raw=True)
    assert "default-src 'self'" in web.header("Content-Security-Policy")
    assert web.header("X-Content-Type-Options") == "nosniff"
    assert web.header("X-Request-ID")


def test_a_missing_document_is_a_404_not_a_crash(web, monkeypatch, tmp_path):
    monkeypatch.setattr(legal, "DOC_DIR", tmp_path)
    status, _ = web.call("GET", "/legal/terms", raw=True)
    assert status == 404


# ----------------------------------------------------------------------------- the renderer is safe


def test_html_in_a_document_is_shown_as_text(web, monkeypatch, tmp_path):
    (tmp_path / "terms.md").write_text(
        "# Title <img src=x onerror=alert(1)>\n\n<script>alert(1)</script>\n\n"
        '[bad](javascript:alert(1)) and [data](data:text/html;base64,AAAA) and [ok](https://example.com/a?b=1&c="2")\n\n'
        "- item <b>bold</b>\n\n| a | b |\n| - | - |\n| <i>x</i> | `<u>y</u>` |\n\n```\n<script>block</script>\n```\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(legal, "DOC_DIR", tmp_path)
    status, body = web.call("GET", "/legal/terms", raw=True)
    text = body.decode("utf-8")
    assert status == 200
    for dangerous in (
        "<script>",
        "<img",
        "<b>",
        "<i>",
        "<u>",
        "javascript:",
        "data:text/html",
        "onerror=alert(1)>",
    ):
        assert dangerous not in text.split("<main", 1)[1], dangerous
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert 'href="https://example.com/a?b=1&amp;c=&quot;2&quot;"' in text
    assert "<li>item &lt;b&gt;bold&lt;/b&gt;</li>" in text
    assert "<table" in text and "&lt;i&gt;x&lt;/i&gt;" in text


def test_renderer_handles_the_markdown_the_documents_use():
    html_out = legal.render(
        "# One\n\n## Two\n\nA **bold** and *italic* line with `code`.\n\n1. first\n2. second\n\n- a\n- b\n\n> a quote\n\n| h1 | h2 |\n| --- | --- |\n| c1 | c2 |\n"
    )
    for fragment in (
        "<h1>One</h1>",
        "<h2>Two</h2>",
        "<strong>bold</strong>",
        "<em>italic</em>",
        "<code>code</code>",
        "<ol><li>first</li><li>second</li></ol>",
        "<ul><li>a</li><li>b</li></ul>",
        "<blockquote>",
        "<th>h1</th>",
        "<td>c2</td>",
    ):
        assert fragment in html_out, fragment


def test_links_between_legal_pages_are_kept_and_other_relative_links_become_text():
    out = legal.render(
        "See [privacy](privacy.md) and [the DPA](../legal/dpa.md#x) and [ops](../multitenant-operations.md) and [here](../../etc/passwd.md)."
    )
    assert '<a href="/legal/privacy">privacy</a>' in out
    assert '<a href="/legal/dpa">the DPA</a>' in out
    assert "multitenant-operations.md" not in out and '<a href="../' not in out
    assert "etc/passwd" not in out


def test_code_spans_are_not_formatted_further():
    out = legal.render("Use `**not bold** and [x](https://a.test)` here.")
    assert "<code>**not bold** and [x](https://a.test)</code>" in out


# ----------------------------------------------------------------------------- the documents


def _links(path: Path) -> list[str]:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    return re.findall(r"\]\(([^)\s]+)\)", text)


@pytest.mark.parametrize("path", HOSTED_DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_in_the_hosted_docs_resolves(path):
    assert path.exists(), path
    for target in _links(path):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        file_part = target.split("#", 1)[0]
        resolved = (path.parent / file_part).resolve()
        assert resolved.exists(), f"{path.relative_to(ROOT)} links to {target}"
        if "#" in target and resolved.suffix == ".md":
            anchor = target.split("#", 1)[1]
            headings = {
                re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-")
                for h in re.findall(
                    r"^#{1,6}\s+(.+)$", resolved.read_text(encoding="utf-8"), flags=re.M
                )
            }
            assert anchor in headings, (
                f"{path.relative_to(ROOT)} links to missing heading {target}"
            )


@pytest.mark.parametrize("path", LEGAL_FILES, ids=lambda p: p.name)
def test_legal_documents_are_marked_as_templates_needing_counsel(path):
    head = path.read_text(encoding="utf-8")[:900]
    assert (
        "TEMPLATE" in head
        and "counsel" in head.lower()
        and "not legal advice" in head.lower()
    )


def test_the_three_open_legal_items_are_listed():
    for name in ("terms.md", "privacy.md", "dpa.md", "automation-and-linkedin.md"):
        head = (DOCS / "legal" / name).read_text(encoding="utf-8")[:1600].lower()
        for item in ("linkedin", "automated applications", "data-processing agreement"):
            assert item in head, f"{name} should list the open item: {item}"
    overview = (DOCS / "multitenant.md").read_text(encoding="utf-8")
    assert "Legal review still needed" in overview
    for item in (
        "LinkedIn position",
        "automated applications in each market",
        "data-processing agreement template",
    ):
        assert item in overview


def test_the_overview_states_what_is_not_built_and_the_resolved_ambiguities():
    text = (DOCS / "multitenant.md").read_text(encoding="utf-8")
    assert "Differences from the scope document and known gaps" in text
    for fragment in (
        "PostgreSQL: what is and is not proven",
        "KMS in production",
        "Remote sign-in is a broker",
        "Deployment artifacts",
        "Resolved ambiguities",
    ):
        assert fragment in text, fragment
    for ambiguity in (
        "Staff seats",
        "MFA by plan",
        "cannot pause",
        "Billing contact sees usage only",
        "End of a trial",
        "calendar months",
    ):
        assert ambiguity in text, ambiguity


# ----------------------------------------------------------------------------- the API reference matches the routes


def _normalized_route(pattern: str) -> str:
    return re.sub(r"\([^()]*\)", "{}", pattern.replace(chr(92) + ".", "."))


def _documented_control_plane() -> set[tuple[str, str]]:
    """Rows of the control-plane tables (everything before the candidate-workspace section)."""
    text = (
        (DOCS / "hosted-api.md")
        .read_text(encoding="utf-8")
        .split("## Candidate workspace routes", 1)[0]
    )
    found = set()
    for method, path in re.findall(
        r"^\| (GET|POST|PUT|PATCH|DELETE) \| `([^`]+)`", text, flags=re.M
    ):
        found.add((method, re.sub(r"\{[^}]*\}", "{}", path)))
    return found


def test_the_api_reference_lists_no_route_that_does_not_exist():
    real = {
        (
            route.method,
            re.sub(r"\{[^}]*\}", "{}", _normalized_route(route.pattern.pattern)),
        )
        for route in CLOUD_ROUTES
    }
    stale = _documented_control_plane() - real
    assert not stale, f"documented but not routed: {sorted(stale)}"


def test_the_api_reference_covers_the_stable_control_plane_routes():
    documented = _documented_control_plane()
    missing = []
    for route in CLOUD_ROUTES:
        if route.handler.__module__.rsplit(".", 1)[-1] not in {
            "auth",
            "org",
            "billing",
            "platform",
            "public",
            "legal",
        }:
            continue
        key = (route.method, _normalized_route(route.pattern.pattern))
        if key not in documented:
            missing.append(key)
    assert not missing, f"regenerate docs/hosted-api.md: {missing}"


# ----------------------------------------------------------------------------- deployment files


DEPLOY = ROOT / "deploy"
ENV_TOKEN = re.compile(
    r"\b(?:AIRA_CLOUD_[A-Z0-9_]+|STRIPE_[A-Z0-9_]+|AIRA_POOLED_[A-Z0-9_]+)\b"
)


def _code_corpus() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "aira_cloud").rglob("*.py")
    )


def _config_variables() -> set[str]:
    text = (ROOT / "aira_cloud" / "config.py").read_text(encoding="utf-8")
    return set(re.findall(r"_(?:env|bool|int)\(\"([A-Z0-9_]+)\"", text))


def test_deployment_files_name_only_variables_the_code_reads():
    corpus = _code_corpus()
    files = [
        DEPLOY / ".env.cloud.example",
        DEPLOY / "docker-compose.cloud.yml",
        DEPLOY / "terraform" / "main.tf",
        *sorted((DEPLOY / "k8s").glob("*.yaml")),
    ]
    unknown = {}
    for path in files:
        for name in set(ENV_TOKEN.findall(path.read_text(encoding="utf-8"))):
            if name not in corpus:
                unknown[f"{path.name}:{name}"] = True
    assert not unknown, sorted(unknown)


def test_every_config_variable_is_in_the_env_example_and_the_runbook():
    example = (DEPLOY / ".env.cloud.example").read_text(encoding="utf-8")
    runbook = (DOCS / "multitenant-operations.md").read_text(encoding="utf-8")
    missing_example = sorted(
        name
        for name in _config_variables()
        if not re.search(rf"^{name}=", example, flags=re.M)
    )
    missing_runbook = sorted(
        name
        for name in _config_variables()
        if f"`{name}`" not in runbook and name not in runbook
    )
    assert not missing_example, f".env.cloud.example lacks {missing_example}"
    assert not missing_runbook, f"multitenant-operations.md lacks {missing_runbook}"


def test_yaml_files_parse_and_have_a_kind():
    yaml = pytest.importorskip("yaml")
    for path in sorted((DEPLOY / "k8s").glob("*.yaml")):
        documents = [
            d
            for d in yaml.safe_load_all(path.read_text(encoding="utf-8"))
            if d is not None
        ]
        assert documents, path.name
        for document in documents:
            assert document.get("apiVersion") and document.get("kind"), path.name
    compose = yaml.safe_load(
        (DEPLOY / "docker-compose.cloud.yml").read_text(encoding="utf-8")
    )
    assert {"web", "worker", "s3", "storage-init", "mailpit"} <= set(
        compose["services"]
    )
    assert (
        compose["services"]["web"]["build"]["target"] == "web"
        and compose["services"]["worker"]["build"]["target"] == "worker"
    )
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "cloud.yml").read_text(encoding="utf-8")
    )
    assert {"lint", "test", "audit"} <= set(workflow["jobs"])


def test_the_secret_manifest_is_not_applied_by_kustomize_and_holds_no_real_values():
    kustomization = (DEPLOY / "k8s" / "kustomization.yaml").read_text(encoding="utf-8")
    assert "secret.example.yaml" not in kustomization.split("resources:", 1)[1]
    secret = (DEPLOY / "k8s" / "secret.example.yaml").read_text(encoding="utf-8")
    assert "sk_live_" not in secret.replace(
        "<sk_live_...>", ""
    ) and "whsec_" not in secret.replace("<whsec_...>", "")


def test_the_dockerfile_copies_only_paths_that_exist_and_runs_as_non_root():
    text = (DEPLOY / "Dockerfile.cloud").read_text(encoding="utf-8")
    for line in text.splitlines():
        match = re.match(r"COPY (?!--from)(.+)", line)
        if match:
            *sources, _dest = match.group(1).split()
            for source in sources:
                assert (ROOT / source).exists(), f"COPY source missing: {source}"
    assert text.count("USER aira") == 2, "both runtime targets drop root"
    assert "AS web" in text and "AS worker" in text
    assert "patchright install chromium" in text.split("AS worker", 1)[1]
    assert "patchright" not in text.split("AS web", 1)[1].split("AS worker", 1)[0]


def test_the_start_script_and_readme_section_exist():
    script = (ROOT / "scripts" / "start_aira_cloud.ps1").read_text(encoding="utf-8")
    assert "aira_cloud serve --worker" in script and "seed-demo" in script
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Hosted (multi-tenant) build" in readme and "docs/multitenant.md" in readme

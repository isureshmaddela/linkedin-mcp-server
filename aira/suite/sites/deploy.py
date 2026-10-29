"""Shipping a site: a ZIP with a deploy kit for every major cloud, a push to GitHub, Netlify and Vercel deploys.

The **deploy kit** (added next to the site's own files, never over them) lets the same folder go anywhere: a
Dockerfile and nginx config (Google Cloud Run, AWS App Runner, Azure Container Apps, Fly.io, Render, any server),
``netlify.toml``, ``vercel.json``, ``wrangler.toml`` (Cloudflare Pages), ``firebase.json``,
``staticwebapp.config.json`` (Azure Static Web Apps), ``amplify.yml`` (AWS Amplify), ``render.yaml`` and a GitHub
Pages workflow, plus a README with the one command for each.

**GitHub** gets the whole site as one commit through the Git Data API (blobs, a tree, a commit, the branch moved to
it), creating the repository when it does not exist and optionally turning on GitHub Pages. **Netlify** receives the
ZIP (``POST /sites/{id}/deploys``); **Vercel** the files inline (``POST /v13/deployments``). Tokens are the person's
own, stored encrypted by the routes; they never appear in a response or an error. The API addresses can be pointed
at a stand-in for tests (``AIRA_GITHUB_API_URL``, ``AIRA_NETLIFY_API_URL``, ``AIRA_VERCEL_API_URL``).
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import urllib.error
import urllib.request
import zipfile
from typing import Any
from urllib.parse import quote

TARGETS = ("github", "netlify", "vercel")
TARGET_LABELS = {"github": "GitHub", "netlify": "Netlify", "vercel": "Vercel"}
TOKEN_PREFIX = "sites_token_"
TIMEOUT = 60
KIT_ONLY_FOR_ZIP = (
    ".github/workflows/pages.yml",
)  # pushing a workflow needs a token with the workflow scope


class DeployError(RuntimeError):
    """A deploy failed in a way a person can act on (502 with this sentence)."""


def slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return (text or "my-site")[:60].strip("-") or "my-site"


# ---------------------------------------------------------------------------
# the deploy kit and the ZIP
# ---------------------------------------------------------------------------


def kit_files(name: str) -> dict[str, str]:
    site = slug(name)
    readme = f"""# {name}

Built with **Monosage Sites**. A static website: HTML, CSS and JavaScript with no build step, so it runs anywhere.

## Run it on your computer

```bash
python -m http.server 8080      # then open http://localhost:8080
# or: npx serve .
```

## Deploy it (pick one)

| Where | How |
| --- | --- |
| **Netlify** | Drag this folder onto app.netlify.com/drop, or `npx netlify-cli deploy --prod --dir .` (uses `netlify.toml`) |
| **Vercel** | `npx vercel --prod` (uses `vercel.json`) |
| **Cloudflare Pages** | `npx wrangler pages deploy . --project-name {site}` (uses `wrangler.toml`) |
| **GitHub Pages** | Push to a GitHub repository; `.github/workflows/pages.yml` publishes every push to `main` (Settings > Pages > Source: GitHub Actions) |
| **Firebase Hosting** | `npx firebase-tools deploy --only hosting` (uses `firebase.json`) |
| **Google Cloud Run** | `gcloud run deploy {site} --source . --allow-unauthenticated` (uses the `Dockerfile`) |
| **AWS Amplify** | Connect the repository in the Amplify console (uses `amplify.yml`) |
| **AWS S3 + CloudFront** | `aws s3 sync . s3://YOUR-BUCKET --delete --exclude ".git/*"` |
| **Azure Static Web Apps** | `npx @azure/static-web-apps-cli deploy . --env production` (uses `staticwebapp.config.json`) |
| **Render** | New > Blueprint, pick the repository (uses `render.yaml`) |
| **Fly.io** | `fly launch --now` (uses the `Dockerfile`) |
| **Any server with Docker** | `docker build -t {site} . && docker run -p 8080:8080 {site}` |

The Docker image is nginx serving the files on port 8080 with compression and caching.
"""
    return {
        "README.md": readme,
        "Dockerfile": "FROM nginx:1.27-alpine\nCOPY nginx.conf /etc/nginx/conf.d/default.conf\n"
        "COPY . /usr/share/nginx/html\nRUN rm -f /usr/share/nginx/html/nginx.conf /usr/share/nginx/html/Dockerfile\n"
        "EXPOSE 8080\n",
        ".dockerignore": ".git\n.github\n*.md\nnetlify.toml\nvercel.json\nwrangler.toml\nfirebase.json\n"
        "staticwebapp.config.json\namplify.yml\nrender.yaml\n",
        "nginx.conf": """server {
    listen 8080;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;
    gzip on;
    gzip_types text/css application/javascript image/svg+xml application/json;
    location / {
        try_files $uri $uri/ $uri.html /index.html;
    }
    location ~* \\.(css|js|png|jpg|jpeg|gif|webp|svg|ico|woff2?)$ {
        expires 7d;
        add_header Cache-Control "public";
    }
}
""",
        "netlify.toml": '[build]\n  publish = "."\n\n[[headers]]\n  for = "/*"\n  [headers.values]\n'
        '    X-Content-Type-Options = "nosniff"\n    Referrer-Policy = "strict-origin-when-cross-origin"\n',
        "vercel.json": json.dumps({"cleanUrls": True, "trailingSlash": False}, indent=2)
        + "\n",
        "wrangler.toml": f'name = "{site}"\npages_build_output_dir = "."\ncompatibility_date = "2026-01-01"\n',
        "firebase.json": json.dumps(
            {
                "hosting": {
                    "public": ".",
                    "cleanUrls": True,
                    "ignore": [
                        "firebase.json",
                        "**/.*",
                        "Dockerfile",
                        "nginx.conf",
                        "*.md",
                        "*.toml",
                        "*.yml",
                        "*.yaml",
                    ],
                }
            },
            indent=2,
        )
        + "\n",
        "staticwebapp.config.json": json.dumps(
            {
                "globalHeaders": {"X-Content-Type-Options": "nosniff"},
                "mimeTypes": {".webmanifest": "application/manifest+json"},
            },
            indent=2,
        )
        + "\n",
        "amplify.yml": "version: 1\nfrontend:\n  phases:\n    build:\n      commands: []\n  artifacts:\n"
        "    baseDirectory: /\n    files:\n      - '**/*'\n",
        "render.yaml": f'services:\n  - type: web\n    name: {site}\n    runtime: static\n    buildCommand: ""\n'
        "    staticPublishPath: .\n",
        ".nojekyll": "",
        ".github/workflows/pages.yml": """name: Deploy to GitHub Pages
on:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: true
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: .
      - id: deployment
        uses: actions/deploy-pages@v4
""",
    }


def bundle(
    files: dict[str, str],
    assets: list[tuple[str, bytes]],
    *,
    name: str,
    with_kit: bool = True,
    target: str = "zip",
) -> dict[str, bytes]:
    """Every file to ship, as bytes: the site, its uploaded assets, and the kit where it does not collide."""
    out: dict[str, bytes] = {
        path: content.encode("utf-8") for path, content in files.items()
    }
    for asset_name, data in assets:
        out.setdefault(f"assets/{asset_name}", data)
    if with_kit:
        for path, content in kit_files(name).items():
            if target != "zip" and path in KIT_ONLY_FOR_ZIP:
                continue
            out.setdefault(path, content.encode("utf-8"))
    return out


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[path])
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _base(target: str) -> str:
    defaults = {
        "github": ("AIRA_GITHUB_API_URL", "https://api.github.com"),
        "netlify": ("AIRA_NETLIFY_API_URL", "https://api.netlify.com/api/v1"),
        "vercel": ("AIRA_VERCEL_API_URL", "https://api.vercel.com"),
    }
    env, default = defaults[target]
    return (os.getenv(env) or default).rstrip("/")


def _call(
    target: str,
    method: str,
    path: str,
    token: str,
    body: Any = None,
    *,
    raw: bytes | None = None,
    content_type: str = "application/json",
    ok: tuple[int, ...] = (200, 201),
) -> tuple[int, Any]:
    data = (
        raw
        if raw is not None
        else (json.dumps(body).encode("utf-8") if body is not None else None)
    )
    request = urllib.request.Request(_base(target) + path, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", content_type)
    request.add_header("User-Agent", "Monosage-Sites/1.0")
    if target == "github":
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed API hosts
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, payload = error.code, error.read() or b""
    except (urllib.error.URLError, OSError) as error:
        raise DeployError(
            f"Could not reach {TARGET_LABELS[target]}: {getattr(error, 'reason', error)}."
        ) from None
    try:
        parsed = json.loads(payload.decode("utf-8") or "null")
    except ValueError:
        parsed = None
    if status not in ok:
        message = ""
        if isinstance(parsed, dict):
            error = parsed.get("error")
            message = str(
                parsed.get("message")
                or (error.get("message") if isinstance(error, dict) else error)
                or ""
            )
        message = message.replace(token, "****")[:300]
        label = TARGET_LABELS[target]
        if status == 401:
            raise DeployError(
                f"{label} refused the token. Create a new one and save it in Deploy."
            )
        if status == 403:
            raise DeployError(
                f"{label} says the token may not do this ({message or 'permission denied'}). Check its scopes."
            )
        raise _Status(
            status, f"{label} answered {status}: {message or 'the request failed'}."
        )
    return status, parsed


class _Status(DeployError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

_REPO_RE = re.compile(
    r"(?:([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/)?([A-Za-z0-9._-]{1,100})"
)


def check_repo(value: Any) -> str:
    text = (
        str(value or "")
        .strip()
        .removeprefix("https://github.com/")
        .removesuffix(".git")
        .strip("/")
    )
    if not _REPO_RE.fullmatch(text) or text.endswith((".", "/")):
        raise ValueError("Name the repository like my-site or your-name/my-site.")
    return text


def github_login(token: str) -> str:
    _status, user = _call("github", "GET", "/user", token)
    return str((user or {}).get("login") or "")


def push_github(
    token: str,
    repo: str,
    files: dict[str, bytes],
    *,
    message: str,
    branch: str = "main",
    private: bool = True,
    pages: bool = False,
    description: str = "",
) -> dict[str, Any]:
    """Commit ``files`` as the whole content of ``branch`` (created with the repository when needed)."""
    login = github_login(token)
    owner, _, name = check_repo(repo).rpartition("/")
    owner = owner or login
    path = f"/repos/{quote(owner)}/{quote(name)}"
    created = False
    try:
        _call("github", "GET", path, token)
    except _Status as error:
        if error.status != 404:
            raise
        create = {
            "name": name,
            "private": private,
            "auto_init": True,
            "description": description[:300],
        }
        endpoint = (
            "/user/repos"
            if owner.lower() == login.lower()
            else f"/orgs/{quote(owner)}/repos"
        )
        _call("github", "POST", endpoint, token, create)
        created = True
    parent = ""
    try:
        _status, ref = _call(
            "github", "GET", f"{path}/git/ref/heads/{quote(branch)}", token
        )
        parent = str(((ref or {}).get("object") or {}).get("sha") or "")
    except _Status as error:
        if error.status not in (404, 409):
            raise
    if not parent:
        # An empty repository has no Git database yet: one file through the contents API starts it.
        readme = files.get("README.md") or b"# Website\n"
        try:
            _call(
                "github",
                "PUT",
                f"{path}/contents/README.md",
                token,
                {
                    "message": "Start the repository",
                    "content": base64.b64encode(readme).decode(),
                    "branch": branch,
                },
            )
            _status, ref = _call(
                "github", "GET", f"{path}/git/ref/heads/{quote(branch)}", token
            )
            parent = str(((ref or {}).get("object") or {}).get("sha") or "")
        except _Status:
            parent = ""
    tree = []
    for file_path in sorted(files):
        _status, blob = _call(
            "github",
            "POST",
            f"{path}/git/blobs",
            token,
            {
                "content": base64.b64encode(files[file_path]).decode(),
                "encoding": "base64",
            },
        )
        tree.append(
            {"path": file_path, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )
    _status, made_tree = _call(
        "github", "POST", f"{path}/git/trees", token, {"tree": tree}
    )
    commit_body: dict[str, Any] = {
        "message": message,
        "tree": made_tree["sha"],
        "parents": [parent] if parent else [],
    }
    _status, commit = _call("github", "POST", f"{path}/git/commits", token, commit_body)
    if parent:
        _call(
            "github",
            "PATCH",
            f"{path}/git/refs/heads/{quote(branch)}",
            token,
            {"sha": commit["sha"], "force": True},
        )
    else:
        _call(
            "github",
            "POST",
            f"{path}/git/refs",
            token,
            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
    result: dict[str, Any] = {
        "repo": f"{owner}/{name}",
        "url": f"https://github.com/{owner}/{name}",
        "commit": commit["sha"],
        "branch": branch,
        "created": created,
        "files": len(files),
    }
    if pages:
        try:
            _status, site = _call(
                "github",
                "POST",
                f"{path}/pages",
                token,
                {"source": {"branch": branch, "path": "/"}},
                ok=(200, 201, 409),
            )
            result["pages_url"] = str(
                (site or {}).get("html_url")
                or f"https://{owner.lower()}.github.io/{name}/"
            )
        except DeployError as error:
            result["pages_error"] = str(error)
    return result


# ---------------------------------------------------------------------------
# Netlify and Vercel
# ---------------------------------------------------------------------------


def deploy_netlify(
    token: str, files: dict[str, bytes], *, site_id: str = ""
) -> dict[str, Any]:
    site: dict[str, Any] | None = None
    if site_id:
        try:
            _status, site = _call("netlify", "GET", f"/sites/{quote(site_id)}", token)
        except _Status as error:
            if error.status != 404:
                raise
            site = None
    if not site:
        _status, site = _call("netlify", "POST", "/sites", token, {})
    site = site or {}
    _status, deploy = _call(
        "netlify",
        "POST",
        f"/sites/{quote(str(site.get('id')))}/deploys",
        token,
        raw=zip_bytes(files),
        content_type="application/zip",
    )
    deploy = deploy or {}
    url = str(
        site.get("ssl_url")
        or site.get("url")
        or deploy.get("ssl_url")
        or deploy.get("deploy_ssl_url")
        or ""
    )
    return {
        "url": url,
        "site_id": str(site.get("id") or ""),
        "deploy_id": str(deploy.get("id") or ""),
        "state": str(deploy.get("state") or "uploaded"),
    }


def deploy_vercel(token: str, files: dict[str, bytes], *, name: str) -> dict[str, Any]:
    body = {
        "name": slug(name),
        "files": [
            {
                "file": path,
                "data": base64.b64encode(data).decode(),
                "encoding": "base64",
            }
            for path, data in sorted(files.items())
        ],
        "projectSettings": {"framework": None},
        "target": "production",
    }
    _status, deploy = _call(
        "vercel",
        "POST",
        "/v13/deployments?skipAutoDetectionConfirmation=1",
        token,
        body,
    )
    deploy = deploy or {}
    alias = deploy.get("alias") or []
    host = str(alias[0] if alias else deploy.get("url") or "")
    return {
        "url": f"https://{host}" if host and not host.startswith("http") else host,
        "deploy_id": str(deploy.get("id") or ""),
        "state": str(deploy.get("readyState") or "QUEUED"),
    }

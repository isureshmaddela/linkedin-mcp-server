# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Monosage (aira)

This repository is a fork of the LinkedIn MCP server; most of it is now Monosage (code name aira). The sections after
this one are upstream's and apply to `linkedin_mcp_server/`.

Layout:

One project, one folder per app (the map is `APPS.md`):

- `auto_apply_assistant.py`: the single-user app, a standard-library `ThreadingHTTPServer` on `127.0.0.1:8765`
  (`uv run python auto_apply_assistant.py`). The shell's browser files (app shell, sign-in, Careers screens, icons)
  are static HTML, CSS and JS in `auto_apply_assistant/` (no build step), with translations in
  `auto_apply_assistant/i18n/`.
- `aira/`: Careers modules (scoring, apply engine, email, documents, schema) and the suite apps under
  `aira/suite/<app>` (Shop, Sites, Social, Videos, Life, Planner, Stocks and the rest), with `aira/suite/kit.py` as
  their shared kit. Each app's screens are in its own `web/` folder (`aira/suite/shop/web/views-shop.js`); the browser
  still loads every file as `/static/<name>` (`aira/webfiles.py`), so file names stay unique.
- `monosage_cloud/`: the hosted, multi-tenant build (`uv run python -m monosage_cloud serve`, port 8780). It imports
  `auto_apply_assistant` and uses `_connect` and `_request_context`, so keep their signatures. `aira_cloud` is its
  old name, kept as an alias.
- `linkedin_mcp_server/`: the upstream MCP server.
- `tests/`: one folder per app (`tests/careers/`, `tests/shop/`, `tests/cloud/`, `tests/shared/`, `tests/linkedin_mcp/`
  ...), with the shared stubs and helpers (`serpapi_stub.py`, `cloud_support.py`, `conftest.py`) in `tests/` itself and
  `tests/js/*.test.mjs` (plain node). Every app folder is on the import path, so tests import helpers by name.

Commands:

- Install: `uv sync --extra cloud --group dev`; Chromium for browser tests: `uv run patchright install chromium`.
- Quick tests (no browser): `uv run pytest -m "not browser" -n 4`. Browser tests: `uv run pytest -m browser -n 4`.
  The whole suite takes about 50 minutes, so run the app you changed: `uv run pytest tests/shop -q -n 4`.
- JS tests: `for f in tests/js/*.test.mjs; do node "$f"; done`.
- Lint and types: `uv run ruff check .`, `uv run ruff format .`, `uv run ty check` (checks the shipped packages,
  configured in `[tool.ty.src]`).
- Dependency audit (as CI runs it): `uv export --frozen --no-dev --extra cloud --no-hashes --no-emit-project -o
  req.txt` then `uvx pip-audit --requirement req.txt --no-deps --disable-pip`.

Test markers: `browser` is applied automatically by `tests/conftest.py` to every test that drives a real Chromium (the
test, a fixture or a module helper calls the patchright launcher); do not add it by hand. `slow` is registered for
tests to opt in to. `--strict-markers` is on, so register any new marker in `tests/conftest.py`.

Schema rules: suite apps create their tables with `kit.ensure(connection, "<app>.vN", statements)` (idempotent
`CREATE TABLE IF NOT EXISTS`, run once per database file; a new table or column is a new key). Careers tables change
only through the versioned migrations in `aira/schema.py` (`MIGRATIONS`, applied by `migrate`); add a new migration
rather than editing an old one.

Never call live paid services from tests (`.env` may hold a real SerpApi key); use the stubs.

## Development Commands

- Use `uv` for dependency management: `uv sync` (dev: `uv sync --group dev`)
- Lint: `uv run ruff check .` (auto-fix with `--fix`)
- Format: `uv run ruff format .`
- Type check: `uv run ty check` (using ty, not mypy)
- Tests: `uv run pytest` (with coverage: `uv run pytest --cov`)
- Aira UI kit logic tests (plain node, no dependencies): `node tests/js/serp_kit.test.mjs`
- Pre-commit: `uv run pre-commit install` then `uv run pre-commit run --all-files`
- Run server locally: `uv run -m linkedin_mcp_server --no-headless`
- Run via uvx (PyPI/package verification only): `uvx linkedin-scraper-mcp`
- Install browser: `uv run patchright install chromium`

## Scraping Rules

- **One section = one navigation.** Each entry in `PERSON_SECTIONS` / `COMPANY_SECTIONS` (`scraping/fields.py`) maps to exactly one page navigation. Never combine multiple URLs behind a single section.
- **Minimize DOM dependence.** Prefer innerText and URL navigation over DOM selectors. When DOM access is unavoidable, use minimal generic selectors (`a[href*="/jobs/view/"]`) — never class names tied to LinkedIn's layout.
- **Detection must be locale-independent.** Classification logic — connection state, action availability, button identity — must rely on URL patterns (`/preload/custom-invite/?vanityName=USER`, `/in/USER/edit/intro/`, `/messaging/compose/`), attribute *presence* (`aria-label` exists, `aria-expanded` exists, `aria-disabled` exists), or structural counts — never on text values like "Connect", "Follow", "Message", "1st", "Pending". The verb in an `aria-label` is locale-dependent; whether the attribute exists is not. Where text is genuinely the only signal, guard it behind an explicit per-locale table and document the limitation in code.

## Tool Return Format

All scraping tools return: `{url, sections: {name: raw_text}}`.

Optional additional keys:

- `references: {section_name: [{kind, url, text?, context?, value?}]}` — LinkedIn URLs are relative paths; `value` carries non-URL identifiers (e.g. company URN id for `kind: "company_urn"`)
- `section_errors: {section_name: {error_type, error_message, issue_template_path, runtime, ...}}`
- `unknown_sections: [name, ...]`
- `job_ids: [id, ...]` (search_jobs only)
- `search_sales_navigator` is the one structured tool: it returns `{url, pages_read, leads: [{name, first_name, last_name, title, company, company_url, company_sales_url, location, profile_url, linkedin_url, degree, tenure_in_role, tenure_in_company, raw_lines}], warnings: [...], section_errors}` instead of `sections`. Its in-page probe keys on `href` structure (`/sales/lead/`, `/sales/company/`) and is read only; the English line patterns that tell a title from a location are the documented locale table in `scraping/sales_navigator.py`.
- `references["feed"]` (get_feed only) — every entry is `kind: "feed_post"`; non-post anchors (sidebar profiles, employer logos) are filtered. URLs may carry either `/feed/update/<urn>/` (DOM-anchor-derived) or `/posts/<slug>` (SDUI-derived) form; both are valid LinkedIn permalinks. Cap is 50 entries, matching `get_feed`'s `num_posts` ceiling.

## Verifying Bug Reports

Always verify scraping bugs end-to-end against live LinkedIn, not just code analysis. Use `uv run`, not `uvx`, so the running process reflects your workspace. Use `uvx` only for packaged distribution verification. Assume a valid login profile already exists at `~/.linkedin-mcp/profile/`.

```bash
# Start server
uv run -m linkedin_mcp_server --transport streamable-http --log-level DEBUG

# Initialize MCP session (grab Mcp-Session-Id from response headers)
curl -s -D /tmp/mcp-headers -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# Extract the session ID from saved headers
SESSION_ID=$(grep -i 'Mcp-Session-Id' /tmp/mcp-headers | awk '{print $2}' | tr -d '\r')

# Call a tool
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_person_profile","arguments":{"linkedin_username":"williamhgates","sections":"posts"}}}'
```

## Release Process

```bash
git checkout main && git pull
uv version --bump minor          # or: major, patch — updates pyproject.toml AND uv.lock
gt create -m "chore: Bump version to X.Y.Z"
gt submit                        # merge PR to trigger release workflow
```

The CI release workflow automatically updates `manifest.json` with the new version — do not update it manually.

After the workflow completes, file a PR in the MCP registry to update the version.

## Commit Messages

- Follow conventional commits: `type(scope): subject`
- Types: feat, fix, docs, style, refactor, test, chore, perf, ci
- Keep subject <50 chars, imperative mood

## Development Workflow

Always read [`CONTRIBUTING.md`](CONTRIBUTING.md) before filing an issue or working on this repository.

- Write a short synthetic prompt that would reproduce the PR diff if given to a fresh Claude Code session. Don't copy the user's first message — distill the conversation into a single instruction that captures the full scope of changes. This tells the maintainer what was intended, which is often more useful than reviewing the full diff. Use a Markdown blockquote under a `## Synthetic prompt` heading, followed by the model attribution:
  ```
  ## Synthetic prompt

  > Add `skills` and `projects` sections to `get_person_profile`, following the certifications PR pattern. Update fields, tests, docs, and manifest.

  Generated with <model name and version>
  ```
- When implementing a new feature/fix:
  1. Check open issues. If no issue exists, create one following the templates in `.github/ISSUE_TEMPLATE/`. Fill in every section; delete optional sections if not applicable.
  2. Branch from `main`: `feature/issue-number-short-description`
  3. Implement and test
  4. Update README.md if relevant
  5. Create a draft PR; only convert to regular PR when ready to merge
  6. Review with AI agents first, then manual review. Do not squash commits.

## PR Reviews

Greptile posts initial reviews as PR review comments, but follow-ups as **issue comments**. Always check both.

```bash
gh api repos/{owner}/{repo}/pulls/{pr}/reviews    # initial reviews
gh api repos/{owner}/{repo}/pulls/{pr}/comments   # inline comments
gh api repos/{owner}/{repo}/issues/{pr}/comments   # follow-up reviews
```

## btca

When you need up-to-date information about technologies used in this project, use btca to query source repositories directly.

```bash
btca resources                           # list available resources
btca ask -r <resource> -q "<question>"
btca ask -r fastmcp -r playwright -q "How do I set up browser context with FastMCP tools?"
```

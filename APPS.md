# Monosage apps: where each one lives

Monosage is one project with one folder per app, like an app suite in one repository: every app shares the same
sign-in, payments, notifications, customer record, translations and app shell, and each app keeps its own code in
its own place.

- **Server code** of an app is its package, for example `aira/suite/shop/`.
- **Screens** (`views-<app>.js`), public pages (`public-<app>.js`) and styles (`app-<app>.css`) are in that
  package's `web/` folder. The browser still loads every file as `/static/<name>`; `aira/webfiles.py` finds it, so
  moving a file never changes an address, and file names are unique across the project.
- **Tests** are in `tests/<app>/`. Shared stubs and helpers stay in `tests/` (`conftest.py` puts every app folder on
  the import path). Run one app's tests with `uv run pytest tests/shop -q -n 4`.
- **The shell** (app switcher, sign-in, settings, the Business hub, translations, icons) is `auto_apply_assistant/`.

| App | Server code | Screens | Tests | Docs |
|---|---|---|---|---|
| Careers (the core product) | `auto_apply_assistant.py`, `aira/*.py` | `auto_apply_assistant/views-careers.js` and the other Careers views | `tests/careers/` (41) | `docs/careers-maturity-scope.md` |
| Leads | `aira/leads/` | `aira/leads/web/` | `tests/leads/` (6) |  |
| API (SerpApi-compatible) | `aira/airaapi/` | `aira/airaapi/web/` | `tests/api/` (11) |  |
| Courses & Events (courses) | `aira/suite/academy/` | `aira/suite/academy/web/` (3) | `tests/academy/` (8) | `docs/apps/academy.md` |
| Ads | `aira/suite/ads/` | `aira/suite/ads/web/` (2) | `tests/ads/` (5) | `docs/apps/ads.md` |
| AI | `aira/suite/ai/` | `aira/suite/ai/web/` (2) | `tests/ai/` (5) | `docs/apps/ai.md` |
| Bookings | `aira/suite/bookings/` | `aira/suite/bookings/web/` (2) | `tests/bookings/` (5) | `docs/apps/bookings.md` |
| Health | `aira/suite/care/` | `aira/suite/care/web/` (3) | `tests/care/` (6) | `docs/apps/care.md` |
| Courses & Events (events) | `aira/suite/events/` | `aira/suite/events/web/` (2) | `tests/events/` (2) | `docs/apps/events.md` |
| Family | `aira/suite/family/` | `aira/suite/family/web/` (2) | `tests/family/` (4) | `docs/apps/family.md` |
| Life stages | `aira/suite/journey/` | `aira/suite/journey/web/` (4) | `tests/journey/` (8) | `docs/apps/journey.md` |
| Learn | `aira/suite/learn/` | `aira/suite/learn/web/` (3) | `tests/learn/` (6) | `docs/apps/learn.md` |
| Marketing | `aira/suite/marketing/` | `aira/suite/marketing/web/` (3) | `tests/marketing/` (7) | `docs/apps/marketing.md` |
| News | `aira/suite/news/` | `aira/suite/news/web/` (3) | `tests/news/` (5) | `docs/apps/news.md` |
| Planner | `aira/suite/planner/` | `aira/suite/planner/web/` (3) | `tests/planner/` (7) | `docs/apps/planner.md` |
| Play | `aira/suite/play/` | `aira/suite/play/web/` (3) | `tests/play/` (7) | `docs/apps/play.md` |
| Service (helpdesk) | `aira/suite/service/` | `aira/suite/service/web/` (3) | `tests/service/` (7) | `docs/apps/service.md` |
| Shop (Commerce) | `aira/suite/shop/` | `aira/suite/shop/web/` (3) | `tests/shop/` (10) | `docs/apps/shop.md` |
| Sites | `aira/suite/sites/` | `aira/suite/sites/web/` (3) | `tests/sites/` (3) | `docs/apps/sites.md` |
| Social and Dating | `aira/suite/social/` | `aira/suite/social/web/` (4) | `tests/social/` (10) | `docs/apps/social.md` |
| Stocks | `aira/suite/stocks/` | `aira/suite/stocks/web/` (3) | `tests/stocks/` (5) | `docs/apps/stocks.md` |
| Studio (Sales) | `aira/suite/studio/` | `aira/suite/studio/web/` (3) | `tests/studio/` (9) | `docs/apps/studio.md` |
| Videos | `aira/suite/videos/` | `aira/suite/videos/web/` (4) | `tests/videos/` (10) | `docs/apps/videos.md` |
| Life (household, profiles, PINs) | `aira/suite/life.py` | `auto_apply_assistant/life.js` | `tests/life/` (3) | |
| Shared platform | `aira/suite/kit.py`, `aira/payments/`, `aira/notify/`, `aira/erasure.py`, `aira/security.py`, `aira/webfiles.py` | `auto_apply_assistant/` (shell) | `tests/shared/` (30) | |
| Monosage Cloud (hosted build) | `monosage_cloud/` | `auto_apply_assistant/cloud-*.js` | `tests/cloud/` (57) | |
| LinkedIn MCP server (upstream) | `linkedin_mcp_server/` |  | `tests/linkedin_mcp/` (25) | |

Careers is still served by the main server module (`auto_apply_assistant.py`); moving it into its own package is the
next step of this layout (docs/platform-review.md, round E).

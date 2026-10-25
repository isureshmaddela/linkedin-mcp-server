# One account, many apps

Aira is organised the way a Google account is: one sign-in, several apps, an app switcher to move between them, and an account page where the apps are managed.

## The pieces

| Piece | What it is | Where |
| --- | --- | --- |
| **Account** | Who you are and what you can use. In the hosted build the account is the organization (owner, admins, coaches, candidates, billing contact, auditor). In the single-user build it is your local sign-in. | `http://127.0.0.1:8780/#/org` (hosted account home) |
| **Apps** | Products the account can open. Each app is one business: **Careers** (find, apply and track), **Leads** (find people who need what you sell), **API** (search engines and keys). | the app switcher (nine-dot grid) in the top bar |
| **App switcher** | Lists the apps your account has, opens one, and links to "More apps" (the Apps page) for admins. | top bar, every screen |
| **Account menu** | Your email and organization, "Manage your account", switch organization, sign out. | top bar, every screen |
| **Apps page** | Where an admin turns apps on and off for the organization. Each app has a price per seat and a short description; subscribing changes the organization's bill with the same proration as a seat change. | `#/org/apps` |
| **Superadmin** | The platform console over every account: tenants, plans, flags, support access, and which apps each tenant has (including overrides). | `#/platform` (operators) |
| **A sub-app installation** | The single-user build (`http://127.0.0.1:8765/`) is one installation of the apps on your own machine. It can stand alone, or sign in with your Aira account so the account at `8780` opens it without a second password. | `8765` |

The catalog of apps is `aira/apps.py`, read by both builds. Each app names its sections (the left menu), the API paths it owns (the hosted gateway refuses them when the organization has not subscribed) and the plan feature that includes it.

## Navigation

- **Careers** app: Dashboard, Jobs, Applications, Interviews, Recruiters, Documents, Analytics, Research (the job-search research workspace, `#/careers`).
- **Leads** app: Find, Leads, Outreach, Settings, History.
- **API** app: Playground, Catalog, History, Locations, Saved and monitors, Account, Keys, Docs, Status, the SERP kit.
- **Account** sections under every app: Settings, Security.
- Hosted roles see their own sections: owners and admins the organization pages (Overview, Candidates, Members, Coaches, Policy, Integrations, Apps, Plan & billing, Audit, Data), coaches their candidates and held questions, operators the platform console.
- Keyboard: `g` then the app's key opens the app (`c` Careers, `l` Leads, `p` API); the section chords inside an app are unchanged (Research inside Careers is `g` then `f`).

## Subscriptions per app (hosted)

A plan (Starter, Team, Enterprise) sets seats, limits and the base price. Apps are turned on per organization:

| App | Included | As an add-on |
| --- | --- | --- |
| Careers | every plan | |
| Leads | Enterprise | Starter and Team: $4 per candidate seat per month |
| API | Enterprise | Team: $3 per candidate seat per month (Starter: not available) |

The trial has every app. An add-on is billed with the plan (one more line on the same subscription), prorated when turned on mid-period, and removed at the period end when turned off. An organization that has not subscribed to an app gets `402 app_required` naming the app on that app's routes, and the app does not appear in its members' switcher. Operators can grant an app to a tenant without charge (a contract) from the platform console.

## Sign in with your Aira account (8780 → 8765)

The single-user build keeps its own accounts and database. To make it part of the account:

1. The local build is told where the account lives (`AIRA_ACCOUNT_URL`, for example `http://127.0.0.1:8780`) and shares a secret with it: `AIRA_ACCOUNT_SECRET` on the local side must equal `AIRA_CLOUD_LAUNCH_SECRET` on the account (32 or more random characters). The account is told where the local installation is: `AIRA_CLOUD_LOCAL_APP_URL`, for example `http://127.0.0.1:8765`. Without both settings on each side the feature is off: the button does not appear and the routes answer `409 not_configured`.
2. The local sign-in page shows "Sign in with your Aira account". That sends the browser to `GET /api/launch/local?return=<local address>` on the account. A signed-in candidate (only a candidate: staff cannot open someone else's installation, and a suspended or read-only organization is refused) gets a one-time launch token (60 seconds, single use, stored hashed, bound to the organization, the member and the exact return address, five per minute) and a `302` back to `<local address>#/launch?token=...`.
3. The local build posts the token to its own `POST /api/auth/launch`, which asks the account `POST /api/internal/launch/validate` (signed with the shared secret and the purpose label `launch`, the same scheme as the agent and broker calls, replay-proof). The account answers once with the person's email, name, organization and role, and audits `auth.launch_local`. The local build then finds or creates the local account for that email (such an account has no password here: a password sign-in answers `403` and says to use the account), starts a normal local session, and reloads into the app. The token never stays in the address bar.
4. `GET /api/auth/me` and `GET /api/apps` on the local build then say `account: {linked, url, org_name}`; the account's app switcher shows "Local workspace" when `AIRA_CLOUD_LOCAL_APP_URL` is set, so the move works in both directions.

The account address must be `http(s)`; off the loopback it must be a public host unless `AIRA_ACCOUNT_ALLOW_LOCAL=1` (a private address you trust). What this is not: it does not merge the two databases (the local workspace stays local), and a person removed from the organization keeps the local account they created, but the next launch is refused (`410`).

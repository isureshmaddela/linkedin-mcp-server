# One account, many apps

Aira is organised the way a Google account is: one sign-in, several apps, an app switcher to move between them, and an account page where the apps are managed.

## The pieces

| Piece | What it is | Where |
| --- | --- | --- |
| **Account** | Who you are and what you can use. In the hosted build the account is the organization (owner, admins, coaches, candidates, billing contact, auditor). In the single-user build it is your local sign-in. | `http://127.0.0.1:8780/#/org` (hosted account home) |
| **Apps** | Products the account can open. Fewer apps that each do more: an app holds several sections, like the clouds of a CRM suite. **Core apps**: **Careers** (find, apply and track), **Developers** (`api`: search engines, keys and connected apps). **Business apps**: **Sales** (`studio`: clients, deals, proposals, invoices), **Marketing** (with **Leads**: find people who need what you sell, content, campaigns, forms), **Service** (cases, help centre, live chat), **Commerce** (**Shop** and **Bookings**), **Courses & Events** (`experiences`: **Academy** and **Events**), **Ads** (advertise on Videos; creators earn a share), **Sites** (describe a website and get it built by the model you choose, then publish or deploy it). **Everyday apps**: **Social** (with **Family**), **Health**, **Learn**, **Play**, **Planner**, **News**, **Videos** (watch, upload and earn), **Life** (`journey`: goals, timeline, document vault), **AI** (chat with Aira, agents with consent levels), **Stocks** (markets, predictions, portfolios, paid stock ideas). | the app switcher (nine-dot grid) in the top bar; only apps the account can open are shown |
| **Business hub** | Every industry app at a glance: revenue this month and by app, what customers did on public pages, each app's setup checklist, the business profile customers see, and the one email outbox all apps share. | `#/hub` (Account, under every app) |
| **Public pages** | What a business shares with its customers without them signing in: a booking page, a storefront, an invoice or proposal, a course page, an event page, a form or a link-in-bio page. Each is `/p/<token>`. | `aira/suite/public.py`, `public.html` |
| **App switcher** | Lists the apps your account has, opens one, and links to "More apps" (the Apps page) for admins. | top bar, every screen |
| **Account menu** | Your email and organization, "Manage your account", switch organization, sign out. | top bar, every screen |
| **Apps page** | Where an admin turns apps on and off for the organization. Each app has a price per seat and a short description; subscribing changes the organization's bill with the same proration as a seat change. | `#/org/apps` |
| **Superadmin** | The platform console over every account: tenants, plans, flags, support access, and which apps each tenant has (including overrides). | `#/platform` (operators) |
| **A sub-app installation** | The single-user build (`http://127.0.0.1:8765/`) is one installation of the apps on your own machine. It can stand alone, or sign in with your Aira account so the account at `8780` opens it without a second password. | `8765` |

The catalog of apps is `aira/apps.py`, read by both builds. Each app names its sections (the left menu), the API paths it owns (the hosted gateway refuses them when the organization has not subscribed) and the plan features it carries (Marketing carries `leads`).

Apps that became sections of a bigger app keep working under their old ids: `LEGACY_IDS` maps `shop` and
`bookings` to `commerce`, `academy` and `events` to `experiences`, `leads` to `marketing` and `family` to `social`.
A subscription or grant stored under an old id moves to the new app the first time the organization's apps are
read (the best of several is kept: paid, then granted, then cancelled), and the billing calls accept either id.

## Everyday (life) apps

For every person, every day, at any age: **Social** (Aira's own social network: profile, friends, feed, chats,
groups and pages; its **Family** section is a private space with a family chat, calendar, birthdays and a board),
**Health** (`care`: medicine reminders with a Taken button, caregivers, readings, diary, appointments, an emergency
card), **Learn** (spaced-repetition flashcards, streaks, quizzes, maths practice, decks on any topic), **Play**
(brain games, a daily challenge, live family trivia, watch and read lists), **Planner** (chores with points,
shared shopping lists, budget, bills, notes), **News** (today's headlines in your language and country, topics and
keywords you follow, headlines read aloud, a child-safe mode) and **Videos** (videos, reels and short films from
people everywhere, your own uploads with a picked frame and captions, subscriptions, playlists, safe ratings for every
age, a public copyright form, and a creator's earnings from the ads on their videos: [apps/videos.md](apps/videos.md)), **Life** (`journey`: goals with steps and check-ins, a life score, a timeline of milestones and an encrypted document vault with expiry reminders: [apps/journey.md](apps/journey.md)) and **AI** (a chat that answers from every app and acts through one agent per app, each with a consent level, approvals and an activity log: [apps/ai.md](apps/ai.md)) and **Stocks** (live quotes, top gainers and losers, each stock's daily log of highs and lows, charts with indicators, forecasts with their track record, a practice portfolio and real holdings with profit and loss, price alerts, and paid subscriptions to the stock ideas a person publishes, with a public track record: [apps/stocks.md](apps/stocks.md)). Shared core ([life-sdk.md](life-sdk.md)):

* **People.** A shared device has a profile per person (Who's using Aira?, an optional PIN); hosted, the person
  is the signed-in member. Private data (health, money, private notes) stays with its person.
* **Today** (`#/today`, the big Home button): each person's day across the apps, with one-tap actions.
* **Easy to use at any age** (`#/display`): easy mode, text size, high contrast, read aloud, less motion, a
  start page. Children and seniors start in easy mode.
* **Live.** Chat, shopping lists and live trivia update while open; a light pulse shows badges and reminders
  (for example a medicine due) as a large dialog that can speak.

Prices (hosted): $2 per seat a month on Starter, $1.50 on Team, included in Enterprise and the trial. Every role
may use them (`life.read` / `life.manage`); platform support access never sees them.

## Languages

Every screen is offered in English, Telugu and Hindi (globe button in the top bar, or Display and reading); see
[i18n.md](i18n.md).

## Business apps

Each business app is one business's tool from first contact to money received, and keeps its data per
organization (in the hosted build the owners, admins and coaches of a business work on the same bookings,
orders or invoices; candidates do not see them). The platform gives every app the same pieces (see
[suite-sdk.md](suite-sdk.md)): a business profile, a revenue ledger the hub adds up, an activity feed, setup
checklists, public pages with rate-limited visitor actions, and an approve-first email outbox that sends
through the business's own mail server. Aira does not process cards: customers pay through the business's
own payment link or instructions, and the business marks payments received.

| App (sections) | For | Highlights |
| --- | --- | --- |
| Sales (Sales) | agencies, freelancers, consultants, every business that sells to clients | clients, a deals pipeline, proposals accepted online, projects with tasks and time, invoices with a pay link, retainers |
| Marketing (Marketing, Leads) | every business that needs customers | leads from Google Maps, public requests, hiring signals and CSV with fit scoring and approved-only outreach; a content calendar, tracked links, lead forms, link-in-bio, newsletters |
| Service (Service) | every business that answers customers | cases from forms, chat and email, a help centre, saved replies, due times, satisfaction ratings |
| Commerce (Shop, Bookings) | shops, makers, salons, clinics, tutors | a storefront with a cart, orders, discount codes and stock; services and deposits, team availability, a booking page, reminders, packages |
| Courses & Events (Courses, Events) | educators, course creators, venues, organisers | courses with lessons, quizzes and certificates, cohorts; ticket types, an event page, check-in, promo codes, sponsors |
| Ads (Ads) | every business that wants to be seen | prepaid campaigns with budgets, CPM or CPC bids, pacing and targeting; skippable video ads, banners and sponsored reels on Videos, reviewed before they run; reports and CSV; a share of every ad goes to the video's creator ([apps/ads.md](apps/ads.md)) |
| Sites (Sites) | freelancers, agencies, small businesses, anyone who needs a website | describe a site and watch it being built by the offline designer, Ollama or LM Studio on your computer, Claude, Gemini, OpenAI, OpenRouter or any OpenAI-compatible model; chat to change it, edit the code, every version kept; publish to a link, download a ZIP ready for any cloud, push to GitHub, deploy to Netlify or Vercel; bill clients for the sites you build ([apps/sites.md](apps/sites.md)) |

## The frame: every app like a Google app

`auto_apply_assistant/shell.js` draws the same frame around every app, like Gmail and Drive under one Google account:

- **Its own address.** Each app opens at `/<path>`: `/careers`, `/developers`, `/sales`, `/marketing`, `/service`, `/commerce`, `/courses`, `/ads`, `/social`, `/health`, `/learn`, `/play`, `/planner`, `/news`, `/videos`, `/life`, `/ai`, `/stocks`; `/home` is Today. Both builds serve the same page there (`aira.apps.APP_PATHS`, `PAGE_PATH_PATTERN`); the page reads the address and opens the app, and moving between apps keeps the address in step (`/stocks#/stocks?tab=watchlist`). Signing in opens Home unless the person chose another start page.
- **Its own logo.** The top bar shows the app's icon and name ("Aira Stocks") and the browser tab the app's name and icon. Every app keeps the suite's icon style (a pale tile with a line icon in the brand green).
- **Search across the top.** It searches the open app (`Aira.appSearch[appId]`: `suggest(query)` fills the list as you type, `run(query)` shows the app's results on Enter). On Home it searches every app at once. Its hint is the handler's `placeholder`; a phone shows `placeholderShort` (or "Search <app>"). Without an app's handler it types into the page's own search field and offers the app's pages and the other apps. `/` focuses it.
- **"+ New" above the menu** (`Aira.appNew[appId]`): the app's main things to create; one item makes the button do it directly, several open a menu.
- **The profile menu.** Everything about the account sits under the avatar at the top right: Manage your account (Settings) and the Account pages (Today, Display and reading, Business hub, and in the hosted build Security, Activity, My workspace). The left menu shows only the open app. A sun/moon button switches the theme.
- **The menu.** A page's tabs (`Aira.suite.tabs`, or `Aira.shell.tabs(section, items, current)` for a page with its own tab bar) also show as folders in the left menu, like a mail app's folders; an app's only section is replaced by its folders. Items are `{ id, label, icon?, count?, href? }`; only the open page's folders show. Home's menu lists every app.
- **The launcher** (the nine dots): Home first, then every app in its color. A click switches in place; Ctrl or middle click opens the app in a new tab.
- The page and its static files are not rate limited (one page is about 90 files and an app may be open in several tabs); the API still is.

## Navigation

- **Careers** app: Dashboard, Jobs, Applications, Interviews, Recruiters, Documents, Analytics, Research (the job-search research workspace, `#/careers`).
- **Developers** app: API (Playground, Catalog, History, Locations, Saved and monitors, Account, Keys, Docs, Status, the SERP kit) and Connected apps.
- **Marketing** app: Marketing and Leads (Find, Leads, Outreach, Settings, History). Each business app lists its sections in the left menu; the switcher shows one tile per app.
- **Account** sections under every app: Settings, Security.
- Hosted roles see their own sections: owners and admins the organization pages (Overview, Candidates, Members, Coaches, Policy, Integrations, Apps, Plan & billing, Audit, Data), coaches their candidates and held questions, operators the platform console.
- Keyboard: `g` then the app's key opens the app (`c` Careers, `p` Developers, `1` Sales, `2` Marketing, `3` Service, `4` Commerce, `5` Courses & Events, `6` Social, `7` Health, `8` Learn, `9` Play; `g` then `0` opens the Business hub); section chords still work from any app (`g` then `l` opens Leads, Research inside Careers is `g` then `f`).

## Who sees what

* The switcher shows only the apps the account can open. An app the organization has not subscribed to is not
  listed; owners and billing contacts find it under **More apps** (`#/org/apps`), with its price and highlights.
* Owners and admins also see Careers: opening it starts their **own workspace** (one candidate seat, only they
  can open it), so the people who run an organization can use it themselves.
* Business apps (Leads included) and the Business hub: owners, admins and coaches (as team members) use them
  on the organization's shared data; auditors read them; candidates and billing contacts do not see them. Leads
  is the business's shared pipeline, so it never reads a person's own LinkedIn session: a Sales Navigator list
  is imported as a CSV file (`403 source_unavailable` explains this).
* A link to a section of an app this person cannot open (`#/social`, `#/academy?tab=overview`) shows the app's
  closed page instead of the app: **not turned on** (with **Open Apps** for owners and billing contacts, "ask an
  owner" for everyone else), **not part of your role** (a candidate opening a business app), or **switched off for
  now** (the superadmin switched it off). The router decides from the section's app (`Aira.apps.blocked`), and a
  view that is still told `402 app_required` or `403 app_disabled` shows the same page instead of an error box.

## Subscriptions per app (hosted)

A plan (Starter, Team, Enterprise) sets seats, limits and the base price. Apps are turned on per organization:

| App | Included | As an add-on |
| --- | --- | --- |
| Careers | every plan | |
| Developers (API) | Enterprise | Team: $3 per candidate seat per month (Starter: not available) |
| Every business app (Sales, Marketing with Leads, Service, Commerce, Courses & Events, Ads, Sites) | Enterprise | Starter: $5, Team: $4 per candidate seat per month |
| Every everyday app (Social, Health, Learn, Play, Planner, News, Videos, Life, AI, Stocks) | Enterprise | Starter: $2, Team: $1.50 per candidate seat per month |

Ad spend is not a seat price: an advertiser prepays an ad balance, billed through the organization's subscription as
it tops up ([apps/ads.md](apps/ads.md)). Creators earn a share of the ads on their videos.

Sending lead email (Leads > Outreach) stays a Team and Enterprise plan feature: Marketing on Starter drafts but
does not send.
The trial has every app. An add-on is billed with the plan (one more line on the same subscription), prorated when turned on mid-period, and removed at the period end when turned off. An organization that has not subscribed to an app gets `402 app_required` naming the app on that app's routes, and the app does not appear in its members' switcher. Operators can grant an app to a tenant without charge (a contract) from the platform console. The platform console also moderates Videos (**Videos moderation**) and runs the creators' money and the ad review
queue (**Creators & ads**, `#/platform/creators`). The superadmin can also **set a subscription** a customer bought outside the product (an invoice, a transfer, a contract): plan, seats, paid months and the apps to grant, in one step (`POST /api/platform/tenants/{id}/subscription`, Tenants > a tenant > Set subscription), recorded in both audit logs.

### The superadmin's catalog controls

The prices above are the defaults. A platform admin changes them, or takes an app away from everyone, in the
platform console under **Apps** (`#/platform/apps`; `GET/PUT/DELETE /api/platform/apps/{id}`):

- **Switch off for everyone.** Nobody can open the app, including trials, Enterprise and operator grants; its
  routes and public links answer `403 app_disabled` ("switched off for now; your data is kept"), it leaves the
  switcher, and it cannot be turned on. It is not billed while off: paying organizations' provider subscriptions
  lose the line from their next period. Their data and add-on choices are kept, so switching it back on (or
  **Reset**) restores both.
- **Prices per plan.** Each plan can sell the app per seat (any price up to $1,000), include it, or not offer it.
  Quotes and the Apps page use a new price at once; paying subscriptions are rewritten at the provider without
  proration, so they pay it from their next period. Only differences from the defaults are stored
  (`platform_apps`, migration 8), and a price set back to the default is no longer a change.
- Careers is part of every plan and cannot be priced or switched off. Every change is in the platform audit log
  (`app.settings_update`, `app.settings_reset`) with the old and new prices and the reason.

## Sign in with your Aira account (8780 → 8765)

The single-user build keeps its own accounts and database. To make it part of the account:

1. The local build is told where the account lives (`AIRA_ACCOUNT_URL`, for example `http://127.0.0.1:8780`) and shares a secret with it: `AIRA_ACCOUNT_SECRET` on the local side must equal `AIRA_CLOUD_LAUNCH_SECRET` on the account (32 or more random characters). The account is told where the local installation is: `AIRA_CLOUD_LOCAL_APP_URL`, for example `http://127.0.0.1:8765`. Without both settings on each side the feature is off: the button does not appear and the routes answer `409 not_configured`.
2. The local sign-in page shows "Sign in with your Aira account". That sends the browser to `GET /api/launch/local?return=<local address>` on the account. A signed-in candidate (only a candidate: staff cannot open someone else's installation, and a suspended or read-only organization is refused) gets a one-time launch token (60 seconds, single use, stored hashed, bound to the organization, the member and the exact return address, five per minute) and a `302` back to `<local address>#/launch?token=...`.
3. The local build posts the token to its own `POST /api/auth/launch`, which asks the account `POST /api/internal/launch/validate` (signed with the shared secret and the purpose label `launch`, the same scheme as the agent and broker calls, replay-proof). The account answers once with the person's email, name, organization and role, and audits `auth.launch_local`. The local build then finds or creates the local account for that email (such an account has no password here: a password sign-in answers `403` and says to use the account), starts a normal local session, and reloads into the app. The token never stays in the address bar.
4. `GET /api/auth/me` and `GET /api/apps` on the local build then say `account: {linked, url, org_name}`; the account's app switcher shows "Local workspace" when `AIRA_CLOUD_LOCAL_APP_URL` is set, so the move works in both directions.

The account address must be `http(s)`; off the loopback it must be a public host unless `AIRA_ACCOUNT_ALLOW_LOCAL=1` (a private address you trust). What this is not: it does not merge the two databases (the local workspace stays local), and a person removed from the organization keeps the local account they created, but the next launch is refused (`410`).

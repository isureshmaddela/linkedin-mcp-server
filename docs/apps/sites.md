# Sites

Describe a website in your own words and watch it being built; then chat to change it, edit the code, go back to
any version, publish it to a link, download it for any cloud, push it to GitHub or deploy it to Netlify or Vercel.
Open it from the app switcher (Business apps > Sites) or at `/sites`.

## Build

The Build page is one big box: **What do you want to build?** Say who the site is for, its pages and sections and the
look you like ("A warm website for Bella Cucina, an Italian restaurant with a menu, photos, reviews and a booking
form"). Under the box:

* **The model** that writes the site (see Models below). The dot is green when it is ready.
* **The stack**: HTML, CSS and JavaScript (default), Tailwind CSS, or React with no build step.
* **＋** adds images (PNG, JPEG, GIF, WebP, SVG or ICO, up to 3 MB each, 20 MB a site). They are saved as
  `assets/<name>` and the model is told about them, so "use my logo" works.
* **Build** (or Ctrl+Enter) makes the site and opens its workspace.

Below the box: ideas to try, **your sites** (with a live thumbnail of each), **start from a template** (twelve
finished sites built offline in one click: restaurant, SaaS, portfolio, shop, gym, event, agency, real estate, clinic,
courses, nonprofit, blog) and your numbers.

## The workspace

On the left is the conversation. While the builder works you see its steps as they happen: reading your request,
its plan, each file as it is written (with its size) and finishing up, with the time and the model. **Stop** ends a
build; files that were finished are kept. When it is done its answer lists the files it changed (click one to open
it in Code) and the version it made. Type the next change and press Enter ("add a pricing page", "make the hero
bolder", "use our logo"); Shift+Enter starts a new line. Any earlier answer has **Preview** and **Restore** for its
version, and a failed one has **Try again**.

On the right:

* **Preview**: the site running for real, in a sandbox (it can never reach your Monosage account). Desktop,
  tablet and phone sizes, a page picker when the site has several pages, reload, and open in a new tab. While a
  build runs, the right side switches to Code so you can watch the code being written, and back to Preview when
  it is done.
* **Code**: every file, with line numbers. Edit and press **Save** (Ctrl+S); Tab indents. Add a file with ＋,
  rename or delete files (the home page, `index.html`, stays), and see uploaded images with the path to use.
  Saving makes a version; typing more in the same file within two minutes keeps that one version.
* **History**: every version (from a build, an edit, a restore, a template or a copy) with the files it changed.
  Preview an older version, restore it (nothing is lost: the current files stay as a version), publish it, or
  download it as a ZIP.
* **Deploy**: see below.

**Details** (top right, or click the name) sets the name, a description, the stack for new changes, and the client
and price when you build the site for someone. On a phone, **Chat** and **Site** switch between the two sides.

## Models

Sites > Models lists every model you can build with. Keys are stored encrypted and never shown again (only the last
four characters). **Test** sends one tiny request, so a wrong key or address shows before a build needs it.

| Model | What you need |
| --- | --- |
| **Monosage designer** | Nothing: it works offline. It builds a polished one-page site from your words (it recognises restaurants, portfolios, SaaS products, agencies, shops, gyms, salons, clinics, events, real estate, courses, nonprofits and blogs) and understands simple changes: colors ("use teal", `#ff6600`), the theme ("make it dark"), sections ("add a FAQ", "remove pricing"), the name ("call it Nova"), the headline ("headline: Fresh bread daily"), the button ("button: Book now") and the font ("elegant", "playful"). For anything else, connect a model. |
| **Ollama** (on this computer) | Install Ollama and pull a model (`ollama pull qwen2.5-coder:7b`). Free and private. When no model is chosen, Sites picks one you have (a coding model first). |
| **LM Studio** (on this computer) | Start LM Studio's local server and load a coding model. |
| **Claude** (Anthropic) | An Anthropic API key. Models: Claude Sonnet 5.5 (default), Opus 5.5, Haiku 5.5, Fable 5.1. The key saved in Settings > Integrations is used if Sites has none. |
| **Gemini** (Google) | A Google AI Studio key. |
| **OpenAI** | An OpenAI API key. |
| **OpenRouter** | One key for hundreds of models from many companies. |
| **Any OpenAI-compatible server** | vLLM, Groq, Together, DeepSeek, Mistral, Azure OpenAI or your own gateway: its address (ending in `/v1`), a model name and a key if it needs one. |
| **Your organization's model** (hosted only) | Nothing: the model your plan includes. |

**Make default** picks the model new builds start with; the picker under the message box changes it for the next
build (each site remembers the model it last used). The picker's model box suggests the models the provider says
your key (or your Ollama) has. Online (hosted), the local models are not offered and a custom address must be a public `https`
address.

## Deploy

* **Publish to a link**: anyone with the link sees the published version. Later changes stay in the preview until you
  press **Update to version N**. **Take offline** turns the link off (publishing again makes a new link). The
  number of visits is shown.
* **Download for any cloud**: a ZIP of the site, with an optional deploy kit next to its files (never over them): a
  `Dockerfile` and `nginx.conf` (port 8080: Google Cloud Run, AWS App Runner, Azure Container Apps, Fly.io, Render,
  any server), `netlify.toml`, `vercel.json`, `wrangler.toml` (Cloudflare Pages), `firebase.json`,
  `staticwebapp.config.json` (Azure Static Web Apps), `amplify.yml` (AWS Amplify), `render.yaml`, a GitHub Pages
  workflow, `.nojekyll`, and a README with the one command each host needs.
* **GitHub**: connect a token (a classic token with the `repo` scope, or a fine-grained one with Contents and
  Administration: read and write), name the repository (`my-site` or `owner/my-site`; it is created if it does not
  exist, private unless you untick it) and the branch, and press **Push to GitHub**: the whole site becomes one
  commit. Tick **Publish with GitHub Pages** to turn Pages on (free plans need a public repository). The GitHub
  Pages workflow is left out of the push (pushing a workflow needs a token with the workflow scope); it is in the
  ZIP.
* **Netlify**: connect a personal access token and press **Deploy to Netlify**; the next deploy updates the same
  site.
* **Vercel**: connect a token and press **Deploy to Vercel** (a production deployment).
* **Client payment**: when the site is for a client with a price, **Mark as paid** records the payment in the
  Business hub's revenue (and **Mark as not paid** takes it back). Monosage does not take card payments: the client
  pays you as you agreed.
* **Recent deploys** lists ZIP downloads, pushes and deploys, with the link or the reason one failed.

## How you earn with Sites

Build websites for clients. Set the client and the price in Details, show them the preview link while you work,
publish or deploy the finished site, and mark it paid: it joins the Business hub's revenue with your other apps.
The hub shows Sites' numbers (sites, published, paid, waiting for payment) and a setup checklist.

## In the Monosage frame

* **+ New**: **New site** (the Build box, ready to type) and **From a template**.
* **Search** across the top finds your sites by name, description or client, and the requests you made in them.
  Enter shows every match in All sites.
* **Folders**: Build, All sites (search, a table with client, price, live and version, and a CSV download) and
  Models.

## Who can use it

* This computer: adults of the household (a child's or teen's profile is refused, like every business app).
* Hosted: sites belong to the organization. Owners, admins and coaches build and deploy; auditors can look; candidates
  and billing contacts cannot open it. The app is sold per seat like the other business apps and is in every trial.

## Safety

* A site's pages are served with their own sandboxing policy (`Content-Security-Policy: sandbox allow-scripts ...`
  without `allow-same-origin`): the site runs in an opaque origin, so its scripts can never read Monosage's cookies or
  call its API. The preview iframe is sandboxed too. Sites use `localStorage` only inside `try`, because the sandbox
  may not allow it.
* File names are checked (relative paths, known text types, no `..`), sizes are capped (60 files, 600 KB a file,
  3 MB a site; images 3 MB each, 20 MB a site), SVG uploads with scripts are refused.
* Model keys and deploy tokens are stored encrypted and never returned, logged or put in an error message.
* Hosted, a custom model address must be public `https` and is checked against private networks before each call.

## Limits and what is not built

* Sites are static (HTML, CSS, JavaScript, React or Tailwind from a CDN). There is no server code, database or form
  inbox: a generated contact form shows a thank-you message or opens the visitor's mail app.
* A build is one model answer. Very large sites may need several requests ("now add the blog page").
* Photos in generated sites come from public photo services by topic, or from your uploads.
* Custom domains are set up at the host you deploy to (Netlify, Vercel, GitHub Pages ...), not in Monosage.
* No live collaboration on the same file; the last save wins (both saves are kept as versions).

## For developers

`aira/suite/sites/`: `store.py` (tables `sb_*`), `providers.py` (one streaming call per API family: Ollama NDJSON,
OpenAI-compatible SSE, Anthropic Messages SSE, Gemini SSE, the platform model), `generator.py` (the system prompt per
stack, the edit context, and the parser of the tagged answer `<plan>`, `<file path>`, `<delete path/>`,
`<summary>`, with fallbacks for Markdown fences and a bare HTML document), `designer.py` (the offline designer),
`templates.py`, `deploy.py` (the kit, the ZIP, GitHub's Git Data API, Netlify, Vercel), `service.py` (projects,
versions, runs, serving), `sites_routes.py` (`/api/sites/*` and the public routes `/api/public/<token>/w/<path>` and
`/v/<n>/<path>`). The page is `auto_apply_assistant/views-sites.js` and `app-sites.css`.

A build is `POST /api/sites/projects/{id}/generate {prompt, provider, model, run_id}`; it answers when the site is
written. Meanwhile `GET /api/sites/runs/{run_id}?from=N` returns the text written after character N, the phase
(thinking, planning, writing, finishing, done), the file being written and the files finished. The page picks the
run id so it can start reading at once, and keeps reading if a proxy cuts the long request. Tests:
`tests/test_suite_sites.py` (unit, local server against stand-ins for every model API and deploy target in
`tests/sites_stubs.py`, hosted roles and isolation) and `tests/test_suite_sites_ui.py` (real browser).

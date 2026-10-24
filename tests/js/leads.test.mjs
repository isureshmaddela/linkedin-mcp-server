// Pure-logic tests for auto_apply_assistant/views-leads.js (Aira.leads.logic).
// No dependencies, no browser: node:assert and a tiny runner.
//
//   node tests/js/leads.test.mjs
//
// The view file is loaded through CommonJS; in node it skips every DOM path and exports its logic object.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const L = require(join(here, "..", "..", "auto_apply_assistant", "views-leads.js"));

let passed = 0;
let failed = 0;
function test(name, fn) {
  try { fn(); passed += 1; console.log(`ok   ${name}`); }
  catch (error) { failed += 1; console.log(`FAIL ${name}\n     ${error && error.stack ? error.stack.split("\n").slice(0, 4).join("\n     ") : error}`); }
}

test("splitList trims, de-duplicates (case-insensitively) and caps", () => {
  assert.deepEqual(L.splitList(" VP Sales, head of sales;\nvp sales ,, CEO "), ["VP Sales", "head of sales", "CEO"]);
  assert.deepEqual(L.splitList(""), []);
  assert.deepEqual(L.splitList(null), []);
  assert.equal(L.splitList("a,b,c,d,e,f,g,h", 3).length, 3);
});

test("the form for each source says what is missing", () => {
  assert.match(L.validateForm("maps", { query: "  " }), /kind of business/);
  assert.equal(L.validateForm("maps", { query: "dentists" }), "");
  assert.match(L.validateForm("people", {}), /job title/);
  assert.equal(L.validateForm("people", { titles: "VP Sales" }), "");
  assert.equal(L.validateForm("people", { company: "Acme" }), "");
  assert.match(L.validateForm("companies", { titles: "x" }), /keyword/);
  assert.equal(L.validateForm("companies", { location: "Austin" }), "");
  assert.match(L.validateForm("hiring", {}), /role/);
  assert.match(L.validateForm("navigator", {}), /Paste/);
  assert.match(L.validateForm("navigator", { url: "https://www.linkedin.com/feed/" }), /not a Sales Navigator/);
  assert.match(L.validateForm("navigator", { url: "https://evil.example/sales/search/people" }), /not a Sales Navigator/);
  assert.match(L.validateForm("navigator", { url: "https://www.linkedin.com.evil.example/sales/search/people" }), /not a Sales Navigator/);
  assert.equal(L.validateForm("navigator", { url: "https://www.linkedin.com/sales/search/people?query=(a:b)" }), "");
  assert.equal(L.validateForm("navigator", { url: "https://www.linkedin.com/sales/lists/people/123" }), "");
});

test("buildPayload sends exactly the fields the server expects", () => {
  assert.deepEqual(L.buildPayload("maps", { query: " coffee  roasters ", location: "New York", ignored: "x" }), { source: "google_maps", query: "coffee roasters", location: "New York" });
  assert.deepEqual(L.buildPayload("maps", { query: "dentists", location: "" }), { source: "google_maps", query: "dentists" });
  assert.deepEqual(L.buildPayload("people", { titles: "VP Sales, Head of Sales", keywords: "SaaS", location: "Texas" }), { source: "google_web", mode: "people", titles: ["VP Sales", "Head of Sales"], keywords: "SaaS", location: "Texas" });
  assert.deepEqual(L.buildPayload("companies", { keywords: "cloud", titles: "ignored" }), { source: "google_web", mode: "companies", keywords: "cloud" });
  assert.deepEqual(L.buildPayload("hiring", { query: "SDR", location: "Austin" }), { source: "google_jobs", query: "SDR", location: "Austin" });
  assert.deepEqual(L.buildPayload("navigator", { url: "https://www.linkedin.com/sales/search/people?x=1", max_pages: "9" }), { source: "sales_navigator", url: "https://www.linkedin.com/sales/search/people?x=1", max_pages: 5 });
  assert.equal(L.buildPayload("navigator", { url: "https://www.linkedin.com/sales/search/people?x=1" }).max_pages, 2);
  assert.throws(() => L.buildPayload("csv", {}), /no search/);
  assert.throws(() => L.buildPayload("nope", {}));
  assert.equal(L.clampPages(0), 2);
  assert.equal(L.clampPages(-3), 1);
  assert.equal(L.clampPages(3.4), 3);
});

test("the cost line follows the dry run and credits are confirmed once per session", () => {
  assert.match(L.planText({ credits: 1, cache_hit: false }, "maps"), /Uses 1 SerpApi search/);
  assert.match(L.planText({ credits: 3, cache_hit: false }, "maps"), /3 SerpApi searches/);
  assert.match(L.planText({ credits: 0, cache_hit: true }, "maps"), /cache/);
  assert.match(L.planText({ credits: 0, cache_hit: false }, "maps"), /No SerpApi search/);
  assert.match(L.planText({ credits: 0 }, "navigator"), /Sales Navigator/);
  assert.equal(L.planText(null, "maps"), "");
  const state = { confirmed: false };
  assert.equal(L.needsConfirm({ credits: 1 }, state), true);
  assert.equal(L.needsConfirm({ credits: 0 }, state), false);
  state.confirmed = true;
  assert.equal(L.needsConfirm({ credits: 1 }, state), false);
});

test("scores map to tones, with no score kept apart from a low one", () => {
  assert.equal(L.scoreTone(null), "none");
  assert.equal(L.scoreTone(undefined), "none");
  assert.equal(L.scoreTone(0), "low");
  assert.equal(L.scoreTone(39), "low");
  assert.equal(L.scoreTone(40), "mid");
  assert.equal(L.scoreTone(70), "high");
});

test("load more keeps the first copy of a lead and never selects saved ones", () => {
  const a = { preview_id: "a", name: "A" };
  const b = { preview_id: "b", name: "B", duplicate_of: { id: "1" } };
  const merged = L.mergeResults([a], [{ preview_id: "a", name: "A again" }, b]);
  assert.deepEqual(merged.map((l) => l.preview_id), ["a", "b"]);
  assert.equal(merged[0].name, "A");
  assert.deepEqual(L.selectable(merged).map((l) => l.preview_id), ["a"]);
});

test("the filter bar becomes a query string and the export carries the same filters without paging", () => {
  assert.equal(L.leadQuery({ list_id: "L1", status: "new", q: "  jane ", min_score: "60", has_email: "true", sort: "score", limit: 25, offset: 0 }), "list_id=L1&status=new&q=jane&min_score=60&has_email=true&sort=score&limit=25");
  assert.equal(L.leadQuery({ limit: 25, offset: 50 }), "limit=25&offset=50");
  assert.equal(L.leadQuery({ status: "", source: "" }, { paging: false }), "");
  assert.equal(L.exportHref("csv", { list_id: "L1", status: "new", sort: "score", limit: 25, offset: 50 }), "/api/leads/export?format=csv&list_id=L1&status=new");
  assert.equal(L.exportHref("xlsx", {}), "/api/leads/export?format=xlsx");
  assert.equal(L.exportHref("anything", {}), "/api/leads/export?format=csv");
});

test("deep links fill a form in and know the server's own source names", () => {
  assert.deepEqual(L.parseDeepLink({ source: "maps", query: "dentists", location: "Austin", other: "x" }), { source: "maps", form: { query: "dentists", location: "Austin" } });
  assert.equal(L.parseDeepLink({ source: "google_maps" }).source, "maps");
  assert.equal(L.parseDeepLink({ source: "google_web", mode: "companies" }).source, "companies");
  assert.equal(L.parseDeepLink({ source: "google_web" }).source, "people");
  assert.equal(L.parseDeepLink({ source: "sales_navigator", url: "u" }).source, "navigator");
  assert.equal(L.parseDeepLink({ source: "javascript:alert(1)" }).source, "");
  assert.deepEqual(L.parseDeepLink({}), { source: "", form: {} });
});

test("links are only ever http(s)", () => {
  assert.equal(L.safeUrl("https://example.com/x"), "https://example.com/x");
  assert.equal(L.safeUrl("javascript:alert(1)"), "");
  assert.equal(L.safeUrl("data:text/html,x"), "");
  assert.equal(L.safeUrl("not a url"), "");
  assert.equal(L.hostOf("https://www.acme.test/about"), "acme.test");
  assert.equal(L.hostOf("nope"), "");
});

test("the ideal customer profile form is turned into lists and a number", () => {
  assert.deepEqual(L.icpPayload({ titles: "CEO, CTO", keywords: "", require: ["website", "bogus"], min_rating: "4.5" }), {
    titles: ["CEO", "CTO"], keywords: [], industries: [], locations: [], exclude_keywords: [], exclude_companies: [], require: ["website"], services: [], min_rating: 4.5,
  });
  assert.deepEqual(L.icpPayload({ services: ["website", "ads"] }).services, ["website", "ads"]);
  assert.equal(L.icpPayload({}).min_rating, null);
  assert.equal(L.icpSummary({ titles: ["a", "b"], keywords: ["c"] }), "2 titles, 1 keyword");
  assert.equal(L.icpSummary({}), "");
});

test("history lines say what a run did", () => {
  assert.equal(L.describeRun({ status: "ok", found: 4, added: 4, duplicates: 0 }), "4 leads found, 4 added");
  assert.equal(L.describeRun({ status: "ok", found: 1, added: 0, duplicates: 1 }), "1 lead found, 0 added, 1 already saved");
  assert.match(L.describeRun({ status: "error", error: "No Sales Navigator access" }), /Failed: No Sales Navigator/);
  assert.equal(L.rangeText(0, 25, 80), "1–25 of 80");
  assert.equal(L.rangeText(0, 0, 0), "0 leads");
});

test("sources map to the server's names and back", () => {
  assert.equal(L.SOURCES.length, 7);
  assert.equal(L.sourceOfApi("google_intent"), "requests");
  assert.equal(L.sourceOfApi("google_web", "companies"), "companies");
  assert.equal(L.sourceOfApi("google_web", ""), "people");
  assert.equal(L.sourceOfApi("google_jobs"), "hiring");
  assert.equal(L.sourceLabel("sales_navigator"), "Sales Navigator");
  assert.equal(L.sourceLabel("something_new"), "something new");
  for (const source of L.SOURCES.filter((s) => s.id !== "csv")) assert.ok(L.FIELDS[source.id], `${source.id} has a form`);
});

const maps = (signals, extra = {}) => ({ id: "l1", kind: "company", name: "Bean Counter", signals, tags: [], ...extra });

test("gaps in a Maps listing are read from its own signals and can filter a result list", () => {
  const bare = maps([{ type: "no_website", text: "No website on its Google listing" }]);
  const fine = maps([{ type: "news", text: "Opened a second shop" }]);
  assert.deepEqual(L.gapsOf(bare), [{ type: "no_website", label: "No website" }]);
  assert.equal(L.hasGap(fine), false);
  assert.deepEqual(L.gapFilter([bare, fine], true), [bare]);
  assert.equal(L.gapFilter([bare, fine], false).length, 2);
});

test("what a lead needs is limited to the services you offer, with the reason shown", () => {
  const lead = maps([
    { type: "no_website", text: "No website on its Google listing" },
    { type: "weak_site", text: "Site audit: not mobile friendly" },
    { type: "thin_reviews", text: "Only 3 reviews" },
  ]);
  const needs = L.needsOf(lead, ["website", "content"], L.FALLBACK_SERVICES);
  assert.deepEqual(needs.map((n) => n.service), ["website", "content"]);
  assert.equal(needs[0].reason, "No website on its Google listing");
  assert.equal(needs[1].reason, "not mobile friendly", "the Site audit prefix is dropped");
  assert.equal(needs[0].label, "Website design and development");
  assert.deepEqual(L.needsOf(lead, [], L.FALLBACK_SERVICES), []);
  assert.deepEqual(L.needsOf(maps([]), ["ads"], L.FALLBACK_SERVICES), []);
  const post = maps([{ type: "request", text: "Need a new site for my bakery" }], { kind: "request", tags: ["website"] });
  assert.equal(L.needsOf(post, ["website"], L.FALLBACK_SERVICES)[0].reason, "Asked for it in a public post");
  const hiring = maps([{ type: "hiring", text: "Hiring a content writer" }]);
  assert.equal(L.needsOf(hiring, ["content"], L.FALLBACK_SERVICES).length, 1);
});

test("public requests show budget, urgency and age as badges", () => {
  const post = { signals: [{ type: "request", text: "Looking for someone to build a shop", url: "https://reddit.com/r/forhire/1" }, { type: "request_detail", text: "The post mentions a budget, says it is urgent, posted 2 days ago" }], source_ref: "https://elsewhere.test" };
  assert.deepEqual(L.requestBadges(post).map((b) => b.text), ["Budget mentioned", "Urgent", "Posted 2 days ago"]);
  assert.equal(L.requestText(post), "Looking for someone to build a shop");
  assert.equal(L.requestUrl(post), "https://reddit.com/r/forhire/1");
  assert.equal(L.requestUrl({ signals: [], source_ref: "https://x.test/a" }), "https://x.test/a");
  assert.deepEqual(L.requestBadges({ signals: [] }), []);
});

test("the requests form needs a service and sends where, freshness and the services", () => {
  assert.match(L.validateForm("requests", { services: [] }) || "", /Choose at least one service/);
  const payload = L.buildPayload("requests", { services: ["website", "ads"], where: [], freshness: "bogus", location: " Austin " });
  assert.deepEqual(payload.services, ["website", "ads"]);
  assert.deepEqual(payload.where, ["reddit", "web"], "defaults when nothing is ticked");
  assert.equal(payload.freshness, "month", "an unknown freshness falls back");
  assert.equal(payload.location, "Austin");
});

test("role chips come from the services you offer, or from all of them when none is chosen", () => {
  const some = L.roleChips(L.FALLBACK_SERVICES, ["ads"]);
  assert.ok(some.includes("PPC specialist"));
  assert.ok(!some.includes("copywriter"));
  const all = L.roleChips(L.FALLBACK_SERVICES, []);
  assert.ok(all.includes("copywriter") && all.includes("PPC specialist"));
  assert.equal(new Set(all).size, all.length, "no duplicates");
});

test("the Write emails summary says how many drafts and why the rest were skipped", () => {
  const out = L.summarizeDrafts({ created: 2, skipped: [{ lead_id: "a", name: "Acme", reason: "No email address." }, { lead_id: "b", reason: "On your do-not-contact list" }] });
  assert.equal(out.headline, "2 drafts written, 2 skipped.");
  assert.deepEqual(out.skipped, [{ name: "Acme", reason: "No email address." }, { name: "A lead", reason: "On your do-not-contact list" }]);
  assert.match(out.hint, /Nothing is sent yet/);
  const none = L.summarizeDrafts({ created: 0, skipped: [] });
  assert.equal(none.headline, "0 drafts written.");
  assert.equal(none.hint, "");
  assert.equal(L.summarizeDrafts({ created: 1 }).headline, "1 draft written.");
});

test("the send button knows what is approved, what went today and what the cap leaves", () => {
  assert.deepEqual(L.outboxState({ draft: 3, approved: 4, sent_today: 18 }, { daily_cap: 20 }), { approved: 4, drafts: 3, sentToday: 18, cap: 20, remaining: 2, willSend: 2, canSend: true });
  assert.equal(L.outboxState({ approved: 4, sent_today: 20 }, { daily_cap: 20 }).canSend, false);
  assert.equal(L.outboxState({ approved: 0, sent_today: 0 }, { daily_cap: 20 }).canSend, false);
  assert.equal(L.outboxState({ approved: 40, sent_today: 0 }, { daily_cap: 50 }).willSend, 10, "at most ten per press");
  assert.equal(L.outboxState({}, {}).cap, 20);
});

test("the sender settings are sent trimmed, with the password only when typed", () => {
  const payload = L.settingsPayload({ from_name: " Sam ", from_email: "sam@x.test ", smtp_host: "smtp.x.test", smtp_port: "465", smtp_security: "ssl", postal_address: "1 Main St", services: ["website"], daily_cap: "30", delay_seconds: "", allow_regulated_regions: false });
  assert.equal(payload.from_name, "Sam");
  assert.equal(payload.smtp_port, 465);
  assert.equal(payload.delay_seconds, 5, "an empty delay falls back to the default");
  assert.ok(!("smtp_password" in payload), "no password key when none was typed");
  assert.ok(!("clear_password" in payload));
  assert.equal(L.settingsPayload({ smtp_password: "s3cret" }).smtp_password, "s3cret");
  assert.equal(L.settingsPayload({ clear_password: true }).clear_password, true);
  assert.equal(L.settingsPayload({}).smtp_security, "starttls");
  assert.equal(L.problemsText([]), "");
  assert.match(L.problemsText(["a mail server", "a postal address"]), /a mail server; a postal address/);
});

test("who wrote a message is named plainly", () => {
  assert.equal(L.authorLabel("model"), "Ollama");
  assert.equal(L.authorLabel("edited"), "Edited by you");
  assert.equal(L.authorLabel("template"), "Template");
  assert.equal(L.authorLabel(undefined), "Template");
  assert.equal(L.OUTCOME_FALLBACK.length, 8);
  assert.ok(L.OUTCOME_FALLBACK.some(([id]) => id === "do_not_call"));
  assert.equal(L.serviceLabel(L.FALLBACK_SERVICES, "social"), "Social media management");
  assert.equal(L.serviceLabel(L.FALLBACK_SERVICES, "unknown_id"), "unknown_id");
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

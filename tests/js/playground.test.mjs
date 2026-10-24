// Logic tests for auto_apply_assistant/views-playground.js (Aira.playground: example values and URL state).
// No browser: the file runs in a vm context with a minimal Aira, the kit's logic object and the real catalog.
//
//   node tests/js/playground.test.mjs
import assert from "node:assert/strict";
import vm from "node:vm";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const require = createRequire(import.meta.url);
const L = require(join(root, "auto_apply_assistant", "views-serp-kit.js"));
const catalogDir = join(root, "aira", "airaapi", "catalog");
const engines = readdirSync(catalogDir)
  .filter((f) => f.endsWith(".json") && f !== "platform.json")
  .flatMap((f) => JSON.parse(readFileSync(join(catalogDir, f), "utf8")).engines);

const Aira = { views: {}, h: () => null, fmt: {}, ui: {}, toast: () => {}, serp: { logic: L, humanize: L.humanize } };
const context = vm.createContext({ Aira, navigator: { platform: "test", userAgent: "node" }, console });
vm.runInContext(readFileSync(join(root, "auto_apply_assistant", "views-playground.js"), "utf8"), context, { filename: "views-playground.js" });
const P = Aira.playground;

let passed = 0;
let failed = 0;
function test(name, fn) {
  try { fn(); passed += 1; console.log(`ok   ${name}`); }
  catch (error) { failed += 1; console.log(`FAIL ${name}\n     ${error && error.stack ? error.stack.split("\n").slice(0, 4).join("\n     ") : error}`); }
}

test("the catalog has every engine the console promises", () => {
  assert.ok(engines.length >= 136, `found ${engines.length}`);
});

test("example values satisfy the form validation for every engine", () => {
  for (const entry of engines) {
    const values = P.exampleFor(entry);
    const { errors } = L.validateParams(entry, values);
    assert.deepEqual(errors, [], `${entry.id}: ${JSON.stringify(errors)} for ${JSON.stringify(values)}`);
    // yahoo_trends takes only paging and display switches, so an empty request is its example
    if (entry.id !== "yahoo_trends") assert.ok(Object.keys(values).length > 0, `${entry.id} got no example values`);
  }
});

test("example values never include envelope names", () => {
  for (const entry of engines) {
    for (const name of Object.keys(P.exampleFor(entry))) assert.ok(!["api_key", "engine", "output", "async", "no_cache"].includes(name), `${entry.id}: ${name}`);
  }
});

test("flight examples carry real, future dates in the engine's format", () => {
  const entry = engines.find((e) => e.id === "google_flights");
  const values = P.exampleFor(entry);
  assert.match(values.outbound_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.ok(new Date(values.outbound_date) > new Date());
  assert.ok(new Date(values.return_date) > new Date(values.outbound_date));
});

test("identifiers become obvious markers, not invented data", () => {
  const entry = engines.find((e) => e.id === "google_ai_overview");
  assert.equal(P.exampleFor(entry).page_token, "REPLACE_WITH_PAGE_TOKEN");
});

test("URL state round-trips parameters and options", () => {
  const params = { q: "solution architect", location: "Austin, Texas, United States", lrad: 25, remote: true };
  const options = { no_cache: true, output: "html", json_restrictor: "jobs_results[].title" };
  const query = P.stateToQuery("google_jobs", params, options, "abc");
  assert.equal(query.tab, "playground");
  assert.equal(query.engine, "google_jobs");
  assert.equal(query["o.no_cache"], "true");
  assert.equal(query.record, "abc");
  const back = P.queryToState(query);
  assert.equal(back.engine, "google_jobs");
  assert.equal(back.record, "abc");
  assert.deepEqual({ ...back.params }, { q: "solution architect", location: "Austin, Texas, United States", lrad: "25", remote: "true" });
  assert.equal(back.options.no_cache, true);
  assert.equal(back.options.output, "html");
});

test("parameters named like page keys are escaped with p.", () => {
  const query = P.stateToQuery("google", { tab: "x", engine: "y", q: "z" }, {}, "");
  assert.equal(query.tab, "playground");
  assert.equal(query.engine, "google");
  assert.equal(query["p.tab"], "x");
  assert.equal(query["p.engine"], "y");
  const back = P.queryToState(query);
  assert.equal(back.params.tab, "x");
  assert.equal(back.params.engine, "y");
});

test("empty values and default options stay out of the URL", () => {
  const query = P.stateToQuery("google", { q: "a", location: "", gl: null }, { output: "json", no_cache: false }, "");
  assert.deepEqual(Object.keys(query).sort(), ["engine", "q", "tab"]);
});

test("summaries lead with the primary query", () => {
  assert.equal(P.summarize({ gl: "us", q: "coffee", hl: "en" }), "coffee · gl: us · hl: en");
  assert.equal(P.summarize({}), "");
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

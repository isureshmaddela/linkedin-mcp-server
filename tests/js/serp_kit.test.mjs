// Pure-logic tests for auto_apply_assistant/views-serp-kit.js (Aira.serp.logic).
// No dependencies, no browser: node:assert and a tiny runner.
//
//   node tests/js/serp_kit.test.mjs
//
// The kit file is loaded through CommonJS; in node it skips every DOM path and exports its logic object.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const kitPath = join(root, "auto_apply_assistant", "views-serp-kit.js");
const catalogDir = join(root, "aira", "airaapi", "catalog");
const require = createRequire(import.meta.url);
const L = require(kitPath);

const engines = readdirSync(catalogDir)
  .filter((f) => f.endsWith(".json") && f !== "platform.json")
  .flatMap((f) => JSON.parse(readFileSync(join(catalogDir, f), "utf8")).engines);
const engine = (id) => {
  const found = engines.find((e) => e.id === id);
  assert.ok(found, `catalog has ${id}`);
  return found;
};

let passed = 0;
let failed = 0;
function test(name, fn) {
  try { fn(); passed += 1; console.log(`ok   ${name}`); }
  catch (error) { failed += 1; console.log(`FAIL ${name}\n     ${error && error.stack ? error.stack.split("\n").slice(0, 4).join("\n     ") : error}`); }
}

// ---------------------------------------------------------------------------------------------------------------
// snippets
// ---------------------------------------------------------------------------------------------------------------
const tricky = { q: `barista "new york" $5 #1 O'Brien \\ back`, location: "Austin, Texas, United States", lrad: 20, chips: ["a", "b"], remote: true, empty: "", none: null };

test("snippets: eight languages with the endpoint, engine and placeholder key", () => {
  const s = L.buildSnippets("google_jobs", tricky, { baseUrl: "http://127.0.0.1:8765/v1" });
  assert.deepEqual(Object.keys(s), L.LANGUAGES);
  assert.equal(L.LANGUAGES.length, 8);
  for (const lang of L.LANGUAGES) {
    assert.ok(s[lang].includes("http://127.0.0.1:8765/v1/search.json"), `${lang} has the endpoint`);
    assert.ok(s[lang].includes("google_jobs"), `${lang} has the engine`);
    assert.ok(s[lang].includes("YOUR_AIRA_API_KEY"), `${lang} has the placeholder key`);
    assert.ok(!s[lang].includes("empty"), `${lang} drops empty values`);
  }
});

test("snippets: engine first, api_key last, arrays joined, booleans as text", () => {
  const pairs = L.paramPairs("google_jobs", { q: "x", chips: ["a", "b"], remote: true, none: null }, "K");
  assert.deepEqual(pairs, [["engine", "google_jobs"], ["q", "x"], ["chips", "a,b"], ["remote", "true"], ["api_key", "K"]]);
});

test("snippets: quoting is right for each language", () => {
  const s = L.buildSnippets("google", { q: `it's "quoted" $x #y \\z` }, { baseUrl: "https://example.test/v1" });
  assert.ok(s.curl.includes(`--data-urlencode 'q=it'\\''s "quoted" $x #y \\z'`), s.curl);
  assert.ok(s.python.includes(`"q": "it's \\"quoted\\" $x #y \\\\z"`), s.python);
  assert.ok(s.javascript.includes(`"q": "it's \\"quoted\\" $x #y \\\\z"`));
  assert.ok(s.ruby.includes(`"q" => "it's \\"quoted\\" $x \\#y \\\\z"`), s.ruby);
  assert.ok(s.php.includes(`'q' => 'it\\'s "quoted" $x #y \\\\z'`), s.php);
  assert.ok(s.go.includes(`params.Set("q", "it's \\"quoted\\" $x #y \\\\z")`), s.go);
  assert.ok(s.java.includes(`params.put("q", "it's \\"quoted\\" $x #y \\\\z");`), s.java);
  assert.ok(s.csharp.includes(`["q"] = "it's \\"quoted\\" $x #y \\\\z",`), s.csharp);
});

test("snippets: direct SerpApi variant and endpoint joining", () => {
  const direct = L.buildSnippets("google", { q: "x" }, { baseUrl: "https://serpapi.com", apiKey: "YOUR_SERPAPI_API_KEY" });
  assert.ok(direct.curl.includes("https://serpapi.com/search.json"));
  assert.ok(direct.curl.includes("YOUR_SERPAPI_API_KEY"));
  assert.equal(L.endpointUrl("http://h/v1/", "/account.json"), "http://h/v1/account.json");
  assert.equal(L.endpointUrl("http://h/v1/search.json"), "http://h/v1/search.json");
});

test("snippets: the api key is never a real value unless the caller passes one", () => {
  const s = L.buildSnippets("google", { q: "x", api_key: "SECRET" }, { baseUrl: "http://h/v1" });
  for (const lang of L.LANGUAGES) assert.ok(!s[lang].includes("SECRET"), `${lang} must ignore an api_key param`);
});

test("snippets: request URL encodes values", () => {
  const url = L.requestUrl("google", { q: "a b&c" }, { baseUrl: "http://h/v1" });
  assert.equal(url, "http://h/v1/search.json?engine=google&q=a%20b%26c&api_key=YOUR_AIRA_API_KEY");
});

test("tokenize: tokens always rebuild the original code", () => {
  const s = L.buildSnippets("google_jobs", tricky, { baseUrl: "http://127.0.0.1:8765/v1" });
  for (const lang of L.LANGUAGES) {
    const tokens = L.tokenize(lang, s[lang]);
    assert.equal(tokens.map((t) => t.text).join(""), s[lang], `${lang} round trip`);
    assert.ok(tokens.some((t) => t.type === "str"), `${lang} has string tokens`);
  }
});

test("tokenize: keywords, comments, flags, numbers", () => {
  const py = L.tokenize("python", 'import requests  # fetch\nx = 20');
  assert.ok(py.some((t) => t.type === "kw" && t.text === "import"));
  assert.ok(py.some((t) => t.type === "com" && t.text === "# fetch"));
  assert.ok(py.some((t) => t.type === "num" && t.text === "20"));
  const curl = L.tokenize("curl", "curl -G --data-urlencode 'a=b'");
  assert.ok(curl.some((t) => t.type === "flag" && t.text === "--data-urlencode"));
  assert.ok(curl.some((t) => t.type === "kw" && t.text === "curl"));
  const js = L.tokenize("javascript", "const a = fetch(url); // go");
  assert.ok(js.some((t) => t.type === "fn" && t.text === "fetch"));
  assert.ok(js.some((t) => t.type === "com"));
});

// ---------------------------------------------------------------------------------------------------------------
// validation
// ---------------------------------------------------------------------------------------------------------------
test("validate: required parameter and message uses the label", () => {
  const r = L.validateParams(engine("google_jobs"), {});
  assert.deepEqual(r.errors.map((e) => e.message), ["Search query is required."]);
  assert.equal(r.byField.q, "Search query is required.");
  assert.deepEqual(L.validateParams(engine("google_jobs"), { q: "barista" }).errors, []);
});

test("validate: requires_any_of groups (one-of and two groups)", () => {
  const lens = L.validateParams(engine("google_lens"), { type: "all" });
  assert.equal(lens.errors.length, 1);
  assert.match(lens.errors[0].message, /^Provide at least one of: .*Image URL.*image ID/i);
  assert.deepEqual(lens.errors[0].group, ["url", "image_id"]);
  assert.equal(L.validateParams(engine("google_lens"), { type: "all", image_id: "abc" }).errors.length, 0);
  // google_shopping marks q required but allows shoprs instead: the sibling satisfies it
  assert.equal(L.validateParams(engine("google_shopping"), { shoprs: "token" }).errors.length, 0);
  assert.equal(L.validateParams(engine("google_shopping"), {}).errors.length, 1);

  const flights = L.validateParams(engine("google_flights"), {});
  assert.equal(flights.errors.filter((e) => e.group).length, 2);
  const partial = L.validateParams(engine("google_flights"), { departure_id: "AUS", type: "1", outbound_date: "2026-11-04", return_date: "2026-11-09" });
  assert.equal(partial.errors.filter((e) => e.group).length, 1, "arrival is still missing");
  assert.equal(L.validateParams(engine("google_flights"), { departure_id: "AUS", arrival_id: "JFK" }).errors.filter((e) => e.group).length, 0);
});

test("validate: types are coerced and bad numbers are reported", () => {
  const e = engine("google_jobs");
  const ok = L.validateParams(e, { q: "x", lrad: "25" });
  assert.equal(ok.params.lrad, 25);
  assert.deepEqual(L.validateParams(e, { q: "x", lrad: "abc" }).errors.map((x) => x.field), ["lrad"]);
  const g = engine("google");
  assert.equal(L.validateParams(g, { q: "x", start: "10" }).params.start, 10);
  assert.match(L.validateParams(g, { q: "x", start: "1.5" }).errors[0].message, /whole number/);
});

test("validate: enums accept only listed values unless the catalog options are truncated", () => {
  const g = engine("google");
  const color = L.validateParams(g, { q: "x", color_scheme: "blue" });
  assert.match(color.errors[0].message, /must be one of: light, dark/);
  assert.equal(L.validateParams(g, { q: "x", color_scheme: "dark" }).errors.length, 0);
  const tbm = g.params.find((p) => p.name === "tbm");
  assert.equal(tbm.options_truncated, true, "fixture assumption: tbm is truncated");
  assert.equal(L.validateParams(g, { q: "x", tbm: "some_new_value" }).errors.length, 0);
});

test("validate: csv with closed options checks every item, free csv does not", () => {
  const d = engine("google_maps_directions");
  assert.equal(L.validateParams(d, { start_addr: "a", end_addr: "b", avoid: ["tolls", "ferries"] }).params.avoid, "tolls,ferries");
  assert.match(L.validateParams(d, { start_addr: "a", end_addr: "b", avoid: "tolls,teleport" }).errors[0].message, /accepts only/);
  const f = L.validateParams(engine("google_flights"), { departure_id: "AUS,JFK", arrival_id: "LAX" });
  assert.equal(f.params.departure_id, "AUS,JFK");
});

test("validate: dates normalise to the engine's format", () => {
  const flights = engine("google_flights").params.find((p) => p.name === "outbound_date");
  assert.equal(L.dateFormatOf(flights), "iso");
  assert.equal(L.normalizeDate("20261104", "iso"), "2026-11-04");
  const images = engine("google_images").params.find((p) => p.name === "start_date");
  assert.equal(L.dateFormatOf(images), "compact");
  assert.equal(L.normalizeDate("2026-11-04", "compact"), "20261104");
  assert.equal(L.normalizeDate("2026-02-30", "iso"), null, "not a real date");
  assert.equal(L.normalizeDate("hello", "iso"), null);
  const r = L.validateParams(engine("google_images"), { q: "x", start_date: "2026-11-04" });
  assert.equal(r.params.start_date, "20261104");
  assert.match(L.validateParams(engine("google_images"), { q: "x", start_date: "11/04/2026" }).errors[0].message, /valid date \(YYYYMMDD\)/);
});

test("validate: booleans, defaults and unknown params", () => {
  const ai = engine("google_ai_mode");
  assert.equal(L.validateParams(ai, { q: "x", continuable: true }).params.continuable, true);
  assert.ok(!("continuable" in L.validateParams(ai, { q: "x", continuable: false }).params), "false equals the default and is omitted");
  assert.equal(L.validateParams(ai, { q: "x", mystery: "1" }).params.mystery, "1", "unknown keys pass through");
  assert.deepEqual(L.coerceValue({ type: "boolean", label: "B" }, "maybe"), { error: "B must be true or false." });
});

test("validate: conflict warnings only when both sides are set", () => {
  const e = engine("google_jobs");
  assert.deepEqual(L.validateParams(e, { q: "x", location: "Austin" }).warnings, []);
  const both = L.validateParams(e, { q: "x", location: "Austin", uule: "w+CAI" });
  assert.equal(both.warnings.length, 1);
  assert.match(both.warnings[0].message, /cannot be combined/);
  assert.equal(both.errors.length, 0, "a conflict is a warning, not a blocker");
});

test("validate: the any-of map and group order", () => {
  const map = L.anyOfGroups(engine("google_lens"));
  assert.ok(map.url && map.image_id);
  assert.deepEqual(L.orderedGroups(engine("google_jobs")), ["Search", "Localization", "Filters", "Pagination"]);
});

test("catalog: every engine builds snippets and validates without throwing", () => {
  assert.ok(engines.length >= 136);
  const types = new Set(["string", "enum", "integer", "csv", "boolean", "number", "location", "date"]);
  for (const e of engines) {
    for (const p of e.params) assert.ok(types.has(p.type), `${e.id}.${p.name} has a supported type (${p.type})`);
    const sample = {};
    for (const p of e.params) if (p.required) sample[p.name] = p.type === "integer" ? 1 : p.type === "date" ? "2026-11-04" : p.type === "boolean" ? true : p.options && p.options.length ? p.options[0].value : "x";
    const r = L.validateParams(e, {});
    assert.ok(Array.isArray(r.errors));
    const s = L.buildSnippets(e.id, sample, { baseUrl: "http://h/v1" });
    assert.equal(Object.keys(s).length, 8);
    L.conflictPairs(e);
    L.anyOfGroups(e);
    L.orderedGroups(e);
  }
});

// ---------------------------------------------------------------------------------------------------------------
// paths, search, flattening
// ---------------------------------------------------------------------------------------------------------------
test("pathToString: dots, indexes and odd keys", () => {
  assert.equal(L.pathToString([]), "");
  assert.equal(L.pathToString(["organic_results", 0, "title"]), "organic_results[0].title");
  assert.equal(L.pathToString(["a", "b c", 2]), 'a["b c"][2]');
  assert.equal(L.pathToString([0, "x"]), "[0].x");
});

const doc = { organic_results: [{ title: "Acme Cloud", link: "https://acme.test", meta: { rank: 1 } }, { title: "Other", link: "https://other.test" }], search_metadata: { id: "abc", status: "Success" } };

test("searchTree: matches keys and values, opens ancestors, honours the limit", () => {
  const r = L.searchTree(doc, "acme");
  assert.deepEqual([...r.matches].sort(), ["organic_results[0].link", "organic_results[0].title"]);
  for (const open of ["", "organic_results", "organic_results[0]"]) assert.ok(r.open.has(open), `opens ${JSON.stringify(open)}`);
  assert.equal(r.count, 2);
  assert.ok(L.searchTree(doc, "status").matches.has("search_metadata.status"), "keys match");
  assert.equal(L.searchTree(doc, "").count, 0);
  assert.equal(L.searchTree({ a: Array.from({ length: 50 }, () => "hit") }, "hit", { limit: 10 }).truncated, true);
});

test("flattenObject and flattenRows: dotted keys, scalars joined, preferred columns first", () => {
  const flat = L.flattenObject({ title: "T", source: { name: "S", icon: "i" }, tags: ["a", "b"], items: [{ title: "x" }, { title: "y" }], deep: { a: { b: { c: 1 } } }, nothing: null });
  assert.equal(flat.title, "T");
  assert.equal(flat["source.name"], "S");
  assert.equal(flat.tags, "a, b");
  assert.equal(flat.items, "x · y");
  assert.equal(flat.nothing, "");
  assert.equal(typeof flat["deep.a"], "string", "depth is capped and stringified");
  const rows = L.flattenRows([{ snippet: "s", title: "t1", position: 1, link: "l1", zzz: 1 }, { title: "t2", position: 2, extra: "e" }]);
  assert.deepEqual(rows.columns.slice(0, 3).map((c) => c.key), ["position", "title", "link"]);
  assert.equal(rows.rows.length, 2);
  const wide = L.flattenRows([Object.fromEntries(Array.from({ length: 30 }, (_, i) => [`k${i}`, i]))], { maxColumns: 10 });
  assert.equal(wide.columns.length, 10);
  assert.equal(wide.hidden, 20);
});

test("rowsToCsv: quoting, newlines and formula neutralising", () => {
  const columns = [{ key: "a" }, { key: "b" }];
  const csv = L.rowsToCsv(columns, [{ a: 'say "hi", ok', b: "line1\nline2" }, { a: "=SUM(A1)", b: "-5" }, { a: "-cmd", b: "@x" }]);
  assert.equal(csv, 'a,b\r\n"say ""hi"", ok","line1\nline2"\r\n\'=SUM(A1),-5\r\n\'-cmd,\'@x');
});

test("findMainList and listCandidates", () => {
  const result = { search_metadata: { id: 1 }, related_searches: [{ query: "a" }], organic_results: [{ title: "1" }, { title: "2" }, { title: "3" }], local_results: { places: [{ title: "p1" }, { title: "p2" }] } };
  assert.equal(L.findMainList(result).key, "organic_results");
  assert.deepEqual(L.listCandidates(result).map((c) => c.key), ["related_searches", "organic_results", "local_results.places"]);
  assert.equal(L.findMainList({ x: [1, 2, 3] }), null);
  assert.equal(L.findMainList({ stuff: [{ a: 1 }], things: [{ a: 1 }, { a: 2 }] }).key, "things", "falls back to the longest list");
  assert.equal(L.findMainList({ local_results: { places: [{ title: "p1" }, { title: "p2" }] } }).key, "local_results.places");
});

// ---------------------------------------------------------------------------------------------------------------
// charts, values, urls
// ---------------------------------------------------------------------------------------------------------------
test("seriesGeometry: scales into the box, breaks lines on nulls", () => {
  const g = L.seriesGeometry([0, 5, 10], { width: 120, height: 60, padLeft: 10, padRight: 10, padTop: 5, padBottom: 5, min: 0, max: 10 });
  assert.equal(g.points.length, 3);
  assert.equal(Math.round(g.points[0].x), 10);
  assert.equal(Math.round(g.points[2].x), 110);
  assert.equal(Math.round(g.points[0].y), 55, "min sits at the bottom of the plot");
  assert.equal(Math.round(g.points[2].y), 5, "max sits at the top");
  assert.ok(g.line.startsWith("M10.0 55.0"));
  const gap = L.seriesGeometry([1, null, 3], { width: 100, height: 50 });
  assert.equal((gap.line.match(/M/g) || []).length, 2, "two segments");
  assert.equal(L.seriesGeometry([], { width: 100, height: 50 }).points.length, 0);
  const flat = L.seriesGeometry([4, 4, 4], { width: 100, height: 50 });
  assert.ok(Number.isFinite(flat.points[0].y), "flat series does not divide by zero");
  const shared = L.seriesGeometry([1, 2], { width: 100, height: 50, count: 4 });
  assert.equal(Math.round(shared.points[1].x), Math.round(100 / 3), "count positions points on a shared axis");
});

test("niceTicks", () => {
  assert.deepEqual(L.niceTicks(0, 100, 4), [0, 25, 50, 75, 100]);
  assert.deepEqual(L.niceTicks(0, 1, 4), [0, 0.25, 0.5, 0.75, 1]);
  assert.deepEqual(L.niceTicks(5, 5), [5]);
  assert.ok(L.niceTicks(173.2, 181.9, 4).every((t) => t >= 173.2 && t <= 181.9));
});

test("values: durations, numbers, ratings, text", () => {
  assert.equal(L.formatDuration(210), "3h 30m");
  assert.equal(L.formatDuration(45), "45m");
  assert.equal(L.formatDuration(120), "2h");
  assert.equal(L.formatDuration(0), "");
  assert.equal(L.parseNumber("$1,234.50"), 1234.5);
  assert.equal(L.parseNumber("abc"), null);
  assert.equal(L.parseNumber(7), 7);
  assert.deepEqual(L.normalizeRating(4.5, 120), { value: 4.5, count: 120 });
  assert.deepEqual(L.normalizeRating("4.2"), { value: 4.2, count: null });
  assert.deepEqual(L.normalizeRating([{ type: "All Times", rating: 4.8, count: 21000 }]), { value: 4.8, count: 21000 });
  assert.deepEqual(L.normalizeRating({ rating: 3, reviews: 10 }), { value: 3, count: 10 });
  assert.equal(L.normalizeRating(null), null);
  assert.equal(L.textOf({ name: "N" }), "N");
  assert.equal(L.textOf(["a", { title: "b" }]), "a, b");
  assert.equal(L.humanize("related_questions"), "Related questions");
  assert.equal(L.pick({ a: "", b: "x" }, "a", "b"), "x");
});

test("urls: links allow http(s) only, images allow https and inline data", () => {
  assert.equal(L.safeLink("https://example.com/a"), "https://example.com/a");
  assert.equal(L.safeLink("http://example.com"), "http://example.com");
  assert.equal(L.safeLink("javascript:alert(1)"), null);
  assert.equal(L.safeLink("data:text/html,<script>"), null);
  assert.equal(L.safeLink("//example.com"), null);
  assert.equal(L.safeImage("https://img.example.com/a.png"), "https://img.example.com/a.png");
  assert.equal(L.safeImage("http://img.example.com/a.png"), null, "plain http images are refused");
  assert.ok(L.safeImage("data:image/png;base64,AAAA"));
  assert.equal(L.safeImage("data:text/html;base64,AAAA"), null);
  assert.equal(L.safeImage("javascript:alert(1)"), null);
});

// ---------------------------------------------------------------------------------------------------------------
// visual section plan
// ---------------------------------------------------------------------------------------------------------------
const plan = (result) => Object.fromEntries(L.planSections(result).map((p) => [p.key, p.renderer]));

test("planSections: every major shape in the spec gets its own renderer", () => {
  const shapes = {
    organic_results: "organic", ai_overview: "aiOverview", answer_box: "answerBox", knowledge_graph: "knowledgeGraph", related_questions: "questions",
    top_stories: "news", news_results: "news", jobs_results: "jobs", shopping_results: "shopping", local_results: "places", place_results: "place",
    images_results: "images", video_results: "videos", best_flights: "flights", other_flights: "flights", price_insights: "priceInsights", properties: "hotels",
    interest_over_time: "trendsTimeline", interest_by_region: "regionBars", reviews: "reviews", articles: "organic", events_results: "events",
    related_searches: "chips", trending_searches: "trendingNow", transcript: "transcript", cited_by: "citedBy",
  };
  const result = Object.fromEntries(Object.keys(shapes).map((k) => [k, k === "ai_overview" || k === "knowledge_graph" || k === "answer_box" || k === "place_results" || k === "interest_over_time" || k === "price_insights" || k === "cited_by" ? { x: 1 } : [{ title: "t" }]]));
  assert.deepEqual(plan(result), shapes);
});

test("planSections: finance needs summary plus graph; markdown yields to text_blocks; metadata is never a section", () => {
  assert.equal(plan({ summary: { stock: "X" }, graph: [{ price: 1 }] }).summary, "finance");
  assert.ok(!("graph" in plan({ summary: { stock: "X" }, graph: [{ price: 1 }] })), "graph is consumed by the finance chart");
  assert.equal(plan({ summary: { a: 1 } }).summary, "tree", "a bare summary is not a quote");
  const ai = plan({ text_blocks: [{ type: "paragraph" }], references: [{ title: "r" }], markdown: "# x" });
  assert.deepEqual(ai, { text_blocks: "aiBlocks" });
  assert.deepEqual(plan({ markdown: "# x" }), { markdown: "markdown" });
  const meta = plan({ search_metadata: {}, search_parameters: {}, search_information: { total_results: 1 }, pagination: { next: "x" }, serpapi_pagination: { next: "y" }, organic_results: [{ title: "t" }] });
  assert.deepEqual(meta, { organic_results: "organic" });
});

test("planSections: unknown keys become cards when titled records, otherwise the JSON tree", () => {
  const r = plan({ mystery_list: [{ title: "a", link: "x" }, { title: "b" }], blob: { a: 1 }, numbers: [1, 2, 3], untitled: [{ a: 1 }], empty: [], nothing: null });
  assert.deepEqual(r, { mystery_list: "cards", blob: "tree", numbers: "tree", untitled: "tree" });
  assert.equal(plan({ error: "No results" }).error, undefined, "a string error is shown as a note, not a section");
  assert.deepEqual(L.planSections(null), []);
});

test("planSections: counts are list lengths and null for objects", () => {
  const sections = L.planSections({ organic_results: [{ title: "a" }, { title: "b" }], answer_box: { title: "x" } });
  assert.deepEqual(sections.map((s) => [s.key, s.count]), [["answer_box", null], ["organic_results", 2]]);
});

// ---------------------------------------------------------------------------------------------------------------
// source hygiene: the kit must stay DOM-safe
// ---------------------------------------------------------------------------------------------------------------
test("source: no innerHTML writes, document.write or eval; the only html attribute is the trusted icon", () => {
  const source = readFileSync(kitPath, "utf8");
  assert.ok(!/\.innerHTML\s*=/.test(source), "no innerHTML assignment");
  assert.ok(!/insertAdjacentHTML|document\.write|\beval\(|new Function\(/.test(source));
  const htmlAttrs = source.match(/\bhtml:\s*`/g) || [];
  assert.equal(htmlAttrs.length, 1, "exactly one html: attribute (the static icon)");
  assert.ok(/ICON_PATHS\[name\]/.test(source));
  assert.ok(!/serpapi\.com\/(manage|dashboard)/.test(source), "no dashboard URLs");
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

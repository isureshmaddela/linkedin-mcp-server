/* API kit — shared SerpApi console components (Aira.serp)
 *
 * Everything the API console, the Playground and Careers share: the catalog-driven parameter form, option
 * form, JSON tree, code snippets, result renderers (visual, table, raw, html, metadata) and small chips.
 *
 * The file has two halves. The first half is pure logic (snippet generation, validation, flattening, chart
 * geometry, search over trees). It touches no DOM and is exported as Aira.serp.logic, and through CommonJS when
 * loaded by node, so tests/js/serp_kit.test.mjs can exercise it without a browser. The second half builds DOM with
 * the h() helper and textContent only; the single exception is the trusted static icon markup below.
 */
"use strict";
(() => {
  const root = typeof window !== "undefined" ? window : globalThis;
  const Aira = root.Aira || (root.Aira = { views: {} });
  const h = (...args) => Aira.h(...args);
  const hasDom = typeof document !== "undefined";

  // ===========================================================================
  // Pure logic
  // ===========================================================================

  const LANGUAGES = ["curl", "python", "javascript", "ruby", "php", "go", "java", "csharp"];
  const LANGUAGE_LABELS = { curl: "cURL", python: "Python", javascript: "JavaScript", ruby: "Ruby", php: "PHP", go: "Go", java: "Java", csharp: "C#" };
  const GROUP_ORDER = ["Search", "Localization", "Filters", "Pagination", "Advanced"];

  const isEmpty = (value) => value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0);
  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const humanize = (key) => {
    const text = String(key).replace(/\[\d*\]/g, "").replace(/[._-]+/g, " ").trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  };
  const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

  /** Number from "$1,234.50", "4.5", 12; null when there is no digit to read. */
  function parseNumber(value) {
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    if (typeof value !== "string") return null;
    const match = value.replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
    return match ? Number(match[0]) : null;
  }

  /** http(s) URLs only: result links must never carry javascript: or data: schemes. */
  function safeLink(url) {
    if (typeof url !== "string") return null;
    const text = url.trim();
    if (!/^https?:\/\//i.test(text)) return null;
    try { new URL(text); } catch { return null; }
    return text;
  }
  /** Images: https URLs and inline raster or svg data URIs (inert inside <img>). Plain http is refused. */
  function safeImage(url) {
    if (typeof url !== "string") return null;
    const text = url.trim();
    if (/^https:\/\//i.test(text)) return safeLink(text);
    if (/^data:image\/(?:png|jpe?g|gif|webp|avif|svg\+xml)[;,]/i.test(text)) return text;
    return null;
  }

  // ---- engine and parameter helpers ------------------------------------------------------------------------

  function dateFormatOf(param) {
    return /YYYYMMDD/.test(param.help || "") ? "compact" : "iso";
  }
  /** Normalise a user date (2026-11-04, 20261104) to the engine's format; null when it is not a real date. */
  function normalizeDate(raw, format) {
    const text = String(raw).trim();
    const match = text.match(/^(\d{4})-?(\d{2})-?(\d{2})$/);
    if (!match) return null;
    const [, y, m, d] = match;
    const date = new Date(Date.UTC(Number(y), Number(m) - 1, Number(d)));
    if (date.getUTCFullYear() !== Number(y) || date.getUTCMonth() !== Number(m) - 1 || date.getUTCDate() !== Number(d)) return null;
    return format === "compact" ? `${y}${m}${d}` : `${y}-${m}-${d}`;
  }
  const optionValues = (param) => (Array.isArray(param.options) ? param.options.map((o) => String(o.value)) : []);
  const closedOptions = (param) => Array.isArray(param.options) && param.options.length > 0 && !param.options_truncated;

  /** Coerce one raw form value to the type the engine expects. Returns {value} or {error}. */
  function coerceValue(param, raw) {
    if (isEmpty(raw)) return { value: undefined };
    const label = param.label || param.name;
    switch (param.type) {
      case "integer": {
        const text = String(raw).trim();
        if (!/^-?\d+$/.test(text)) return { error: `${label} must be a whole number.` };
        return { value: Number(text) };
      }
      case "number": {
        const text = String(raw).trim();
        const n = Number(text);
        if (text === "" || !Number.isFinite(n)) return { error: `${label} must be a number.` };
        return { value: n };
      }
      case "boolean": {
        if (raw === true || raw === "true" || raw === 1 || raw === "1") return { value: true };
        if (raw === false || raw === "false" || raw === 0 || raw === "0") return { value: false };
        return { error: `${label} must be true or false.` };
      }
      case "date": {
        const format = dateFormatOf(param);
        const value = normalizeDate(raw, format);
        if (!value) return { error: `${label} must be a valid date (${format === "compact" ? "YYYYMMDD" : "YYYY-MM-DD"}).` };
        return { value };
      }
      case "enum": {
        const value = String(raw).trim();
        if (closedOptions(param) && !optionValues(param).includes(value)) {
          const known = optionValues(param);
          return { error: `${label} must be one of: ${known.slice(0, 8).join(", ")}${known.length > 8 ? ", …" : ""}.` };
        }
        return { value };
      }
      case "csv": {
        const items = (Array.isArray(raw) ? raw : String(raw).split(",")).map((item) => String(item).trim()).filter(Boolean);
        if (!items.length) return { value: undefined };
        if (closedOptions(param)) {
          const known = optionValues(param);
          const bad = items.filter((item) => !known.includes(item));
          if (bad.length) return { error: `${label} accepts only: ${known.join(", ")}.` };
        }
        return { value: items.join(",") };
      }
      default:
        return { value: String(raw).trim() };
    }
  }

  /** The fields whose help text says "Cannot be combined with x / y": non-blocking conflict warnings. */
  function conflictPairs(entry) {
    const names = new Set(entry.params.map((p) => p.name));
    const pairs = [];
    for (const param of entry.params) {
      const match = (param.help || "").match(/cannot be (?:combined|used) with ([^.;]+)/i);
      if (!match) continue;
      for (const token of match[1].split(/[\s,/]+|\bor\b|\band\b/i)) {
        const name = token.replace(/[`'"()]/g, "");
        if (name && name !== param.name && names.has(name)) pairs.push([param.name, name]);
      }
    }
    return pairs;
  }

  /**
   * Mirror of the backend validation rules: required parameters, requires_any_of groups, type coercion and enum
   * membership. Returns {params, errors:[{field,message}], warnings:[{field,message}], byField}. Unknown keys pass
   * through untouched (the backend decides unless allow_unknown is set).
   */
  function validateParams(entry, values) {
    const errors = [];
    const warnings = [];
    const params = {};
    const known = new Map(entry.params.map((p) => [p.name, p]));
    const input = values || {};
    for (const [name, raw] of Object.entries(input)) {
      const param = known.get(name);
      if (!param) { if (!isEmpty(raw)) params[name] = raw; continue; }
      const { value, error } = coerceValue(param, raw);
      if (error) errors.push({ field: name, message: error });
      else if (value !== undefined && !(param.type === "boolean" && value === false && param.default !== true)) params[name] = value;
    }
    const present = (name) => params[name] !== undefined && params[name] !== null && params[name] !== "";
    const reported = new Set(errors.map((e) => e.field));
    const alternatives = anyOfGroups(entry);
    for (const param of entry.params) {
      // a required parameter that is one alternative in a requires_any_of group is satisfied by its siblings
      if (param.required && !alternatives[param.name] && !present(param.name) && !reported.has(param.name)) {
        errors.push({ field: param.name, message: `${param.label || param.name} is required.` });
      }
    }
    for (const group of entry.requires_any_of || []) {
      if (group.some(present)) continue;
      if (group.length === 1 && errors.some((e) => e.field === group[0])) continue;
      const labels = group.map((name) => (known.get(name) || {}).label || name);
      errors.push({
        field: group[0],
        group,
        message: group.length === 1 ? `${labels[0]} is required.` : `Provide at least one of: ${labels.join(", ")}.`,
      });
    }
    for (const [a, b] of conflictPairs(entry)) {
      if (present(a) && present(b)) {
        const label = (name) => known.get(name).label || name;
        if (!warnings.some((w) => w.fields && w.fields.includes(a) && w.fields.includes(b))) {
          warnings.push({ field: a, fields: [a, b], message: `${label(a)} cannot be combined with ${label(b)}.` });
        }
      }
    }
    const byField = {};
    for (const error of errors) if (!byField[error.field]) byField[error.field] = error.message;
    return { params, errors, warnings, byField };
  }

  /** Per-field role in the requires_any_of groups, used for the "one of" markers. */
  function anyOfGroups(entry) {
    const map = {};
    for (const group of entry.requires_any_of || []) {
      if (group.length < 2) continue;
      for (const name of group) (map[name] = map[name] || []).push(group);
    }
    return map;
  }

  function orderedGroups(entry) {
    const seen = [];
    for (const param of entry.params) if (!seen.includes(param.group || "Search")) seen.push(param.group || "Search");
    return [...GROUP_ORDER.filter((g) => seen.includes(g)), ...seen.filter((g) => !GROUP_ORDER.includes(g))];
  }

  // ---- code snippets ---------------------------------------------------------------------------------------

  const stringifyParam = (value) => {
    if (Array.isArray(value)) return value.join(",");
    if (isObject(value)) return JSON.stringify(value);
    return String(value);
  };

  /** [[name, string]] in request order: engine first, api_key last, empties dropped. */
  function paramPairs(engineId, params, apiKey) {
    const pairs = [["engine", engineId]];
    for (const [name, value] of Object.entries(params || {})) {
      if (name === "engine" || name === "api_key" || isEmpty(value)) continue;
      pairs.push([name, stringifyParam(value)]);
    }
    pairs.push(["api_key", apiKey]);
    return pairs;
  }

  function endpointUrl(baseUrl, path) {
    const base = String(baseUrl || "").replace(/\/+$/, "");
    if (/\.json$/.test(base)) return base;
    return `${base}${path || "/search.json"}`;
  }

  const q = {
    json: (s) => JSON.stringify(s),
    shell: (s) => `'${String(s).replaceAll("'", "'\\''")}'`,
    php: (s) => `'${String(s).replaceAll("\\", "\\\\").replaceAll("'", "\\'")}'`,
    ruby: (s) => JSON.stringify(s).replaceAll("#", "\\#"),
  };

  function buildSnippets(engineId, params, options = {}) {
    const apiKey = options.apiKey || "YOUR_AIRA_API_KEY";
    const fallbackBase = hasDom && root.location && root.location.origin ? `${root.location.origin}/v1` : "http://127.0.0.1:8765/v1";
    const url = endpointUrl(options.baseUrl || fallbackBase, options.path);
    const pairs = paramPairs(engineId, params, apiKey);
    const last = pairs.length - 1;
    const each = (fn) => pairs.map(([k, v], i) => fn(k, v, i === last, i));

    const curl = [`curl -G ${q.shell(url)} \\`, ...each((k, v, end) => `  --data-urlencode ${q.shell(`${k}=${v}`)}${end ? "" : " \\"}`)].join("\n");

    const python = [
      "import requests",
      "",
      "params = {",
      ...each((k, v) => `    ${q.json(k)}: ${q.json(v)},`),
      "}",
      "",
      `response = requests.get(${q.json(url)}, params=params, timeout=60)`,
      "response.raise_for_status()",
      "data = response.json()",
      'print(data.get("search_metadata", {}).get("status"))',
    ].join("\n");

    const javascript = [
      "const params = new URLSearchParams({",
      ...each((k, v) => `  ${q.json(k)}: ${q.json(v)},`),
      "});",
      "",
      `const response = await fetch(${q.json(url)} + "?" + params.toString());`,
      "if (!response.ok) throw new Error(`HTTP ${response.status}`);",
      "const data = await response.json();",
      "console.log(data.search_metadata?.status);",
    ].join("\n");

    const ruby = [
      'require "net/http"',
      'require "json"',
      "",
      `uri = URI(${q.ruby(url)})`,
      "uri.query = URI.encode_www_form(",
      ...each((k, v, end) => `  ${q.ruby(k)} => ${q.ruby(v)}${end ? "" : ","}`),
      ")",
      "",
      "response = Net::HTTP.get_response(uri)",
      'raise "HTTP #{response.code}" unless response.is_a?(Net::HTTPSuccess)',
      "data = JSON.parse(response.body)",
      'puts data.dig("search_metadata", "status")',
    ].join("\n");

    const php = [
      "<?php",
      "$params = [",
      ...each((k, v) => `    ${q.php(k)} => ${q.php(v)},`),
      "];",
      "",
      `$url = ${q.php(url)} . '?' . http_build_query($params);`,
      "$response = file_get_contents($url);",
      "if ($response === false) {",
      "    throw new RuntimeException('Request failed');",
      "}",
      "$data = json_decode($response, true);",
      "echo $data['search_metadata']['status'] ?? '', PHP_EOL;",
    ].join("\n");

    const go = [
      "package main",
      "",
      "import (",
      '\t"fmt"',
      '\t"io"',
      '\t"net/http"',
      '\t"net/url"',
      ")",
      "",
      "func main() {",
      "\tparams := url.Values{}",
      ...each((k, v) => `\tparams.Set(${q.json(k)}, ${q.json(v)})`),
      "",
      `\tresponse, err := http.Get(${q.json(url)} + "?" + params.Encode())`,
      "\tif err != nil {",
      "\t\tpanic(err)",
      "\t}",
      "\tdefer response.Body.Close()",
      "\tbody, _ := io.ReadAll(response.Body)",
      "\tfmt.Println(response.StatusCode, string(body))",
      "}",
    ].join("\n");

    const java = [
      "import java.net.URI;",
      "import java.net.URLEncoder;",
      "import java.net.http.HttpClient;",
      "import java.net.http.HttpRequest;",
      "import java.net.http.HttpResponse;",
      "import java.nio.charset.StandardCharsets;",
      "import java.util.LinkedHashMap;",
      "import java.util.Map;",
      "import java.util.stream.Collectors;",
      "",
      "public class Search {",
      "    public static void main(String[] args) throws Exception {",
      "        Map<String, String> params = new LinkedHashMap<>();",
      ...each((k, v) => `        params.put(${q.json(k)}, ${q.json(v)});`),
      "",
      "        String query = params.entrySet().stream()",
      "            .map(e -> URLEncoder.encode(e.getKey(), StandardCharsets.UTF_8) + \"=\" + URLEncoder.encode(e.getValue(), StandardCharsets.UTF_8))",
      "            .collect(Collectors.joining(\"&\"));",
      `        HttpRequest request = HttpRequest.newBuilder(URI.create(${q.json(url)} + "?" + query)).GET().build();`,
      "        HttpResponse<String> response = HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofString());",
      "        System.out.println(response.statusCode());",
      "        System.out.println(response.body());",
      "    }",
      "}",
    ].join("\n");

    const csharp = [
      "using System;",
      "using System.Collections.Generic;",
      "using System.Linq;",
      "using System.Net.Http;",
      "",
      "var parameters = new Dictionary<string, string>",
      "{",
      ...each((k, v) => `    [${q.json(k)}] = ${q.json(v)},`),
      "};",
      "",
      'var query = string.Join("&", parameters.Select(p => $"{Uri.EscapeDataString(p.Key)}={Uri.EscapeDataString(p.Value)}"));',
      "using var client = new HttpClient();",
      `var response = await client.GetAsync(${q.json(url)} + "?" + query);`,
      "response.EnsureSuccessStatusCode();",
      "Console.WriteLine(await response.Content.ReadAsStringAsync());",
    ].join("\n");

    return { curl, python, javascript, ruby, php, go, java, csharp };
  }

  /** Plain-GET URL for a search (api_key masked) — handy for "copy request URL". */
  function requestUrl(engineId, params, options = {}) {
    const url = endpointUrl(options.baseUrl || "http://127.0.0.1:8765/v1", options.path);
    const query = paramPairs(engineId, params, options.apiKey || "YOUR_AIRA_API_KEY").map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
    return `${url}?${query.join("&")}`;
  }

  // ---- syntax tint -------------------------------------------------------------------------------------------

  const KEYWORDS = {
    curl: ["curl"],
    python: ["import", "from", "as", "def", "return", "if", "else", "elif", "for", "in", "while", "try", "except", "with", "None", "True", "False", "print"],
    javascript: ["const", "let", "var", "await", "async", "new", "if", "else", "return", "throw", "function", "null", "true", "false", "import", "from", "of"],
    ruby: ["require", "raise", "unless", "if", "else", "end", "do", "def", "nil", "true", "false", "puts"],
    php: ["echo", "throw", "new", "if", "else", "return", "function", "null", "true", "false", "foreach"],
    go: ["package", "import", "func", "var", "if", "else", "return", "defer", "nil", "range", "for", "go"],
    java: ["import", "public", "class", "static", "void", "new", "throws", "String", "Map", "if", "else", "return", "null", "true", "false"],
    csharp: ["using", "var", "new", "await", "async", "string", "if", "else", "return", "null", "true", "false", "class"],
  };
  const LINE_COMMENT = { curl: ["#"], python: ["#"], ruby: ["#"], javascript: ["//"], php: ["//", "#"], go: ["//"], java: ["//"], csharp: ["//"] };

  /** Split code into [{type, text}] tokens (kw, str, com, num, fn, flag, plain); joining the text restores the code. */
  function tokenize(lang, code) {
    const tokens = [];
    const keywords = new Set(KEYWORDS[lang] || []);
    const comments = LINE_COMMENT[lang] || [];
    const push = (type, text) => {
      const last = tokens[tokens.length - 1];
      if (last && last.type === type && type === "plain") last.text += text; else tokens.push({ type, text });
    };
    let i = 0;
    while (i < code.length) {
      const rest = code.slice(i);
      const comment = comments.find((c) => rest.startsWith(c));
      if (comment) {
        const end = code.indexOf("\n", i);
        const stop = end === -1 ? code.length : end;
        push("com", code.slice(i, stop));
        i = stop;
        continue;
      }
      const ch = code[i];
      if (ch === '"' || ch === "'" || (ch === "`" && lang === "javascript")) {
        let j = i + 1;
        while (j < code.length && code[j] !== ch && code[j] !== "\n") {
          if (code[j] === "\\" && lang !== "curl") j += 1;
          j += 1;
        }
        j = Math.min(code.length, j + 1);
        push("str", code.slice(i, j));
        i = j;
        continue;
      }
      if (/[0-9]/.test(ch) && !/[\w]/.test(code[i - 1] || "")) {
        const m = rest.match(/^\d+(?:\.\d+)?/);
        push("num", m[0]);
        i += m[0].length;
        continue;
      }
      if (lang === "curl" && ch === "-" && /^-{1,2}[A-Za-z][\w-]*/.test(rest)) {
        const m = rest.match(/^-{1,2}[A-Za-z][\w-]*/);
        push("flag", m[0]);
        i += m[0].length;
        continue;
      }
      if (/[A-Za-z_$]/.test(ch)) {
        const m = rest.match(/^[A-Za-z_$][\w$]*/);
        const word = m[0];
        const after = code.slice(i + word.length).match(/^\s*\(/);
        if (keywords.has(word)) push("kw", word);
        else if (after) push("fn", word);
        else push("plain", word);
        i += word.length;
        continue;
      }
      push("plain", ch);
      i += 1;
    }
    return tokens;
  }

  // ---- JSON paths and tree search ---------------------------------------------------------------------------

  const IDENT = /^[A-Za-z_$][\w$]*$/;
  /** organic_results[0].title — JS-style access path, no leading dot. */
  function pathToString(path) {
    let out = "";
    for (const part of path) {
      if (typeof part === "number") out += `[${part}]`;
      else if (IDENT.test(part)) out += out ? `.${part}` : part;
      else out += `[${JSON.stringify(part)}]`;
    }
    return out;
  }

  /**
   * Find matches for a tree filter. Returns {matches:Set<pathString>, open:Set<pathString>, count, truncated}:
   * `matches` are the nodes whose key or primitive value contains the text, `open` holds every ancestor path so the
   * tree can expand just far enough to reveal them.
   */
  function searchTree(value, query, { limit = 500 } = {}) {
    const needle = String(query || "").trim().toLowerCase();
    const result = { matches: new Set(), open: new Set(), count: 0, truncated: false };
    if (!needle) return result;
    const stack = [{ node: value, path: [] }];
    while (stack.length) {
      const { node, path } = stack.pop();
      if (result.count >= limit) { result.truncated = true; break; }
      const key = path.length ? path[path.length - 1] : null;
      const keyText = typeof key === "string" ? key.toLowerCase() : "";
      const primitive = node === null || typeof node !== "object";
      const valueText = primitive ? String(node).toLowerCase() : "";
      if ((keyText && keyText.includes(needle)) || (primitive && valueText.includes(needle))) {
        const self = pathToString(path);
        result.matches.add(self);
        result.count += 1;
        for (let n = 0; n < path.length; n += 1) result.open.add(pathToString(path.slice(0, n)));
      }
      if (!primitive) {
        const entries = Array.isArray(node) ? node.map((v, i) => [i, v]) : Object.entries(node);
        for (let n = entries.length - 1; n >= 0; n -= 1) stack.push({ node: entries[n][1], path: [...path, entries[n][0]] });
      }
    }
    return result;
  }

  // ---- flattening for table mode and CSV ---------------------------------------------------------------------

  const TABLE_PREFERRED = ["position", "rank", "title", "name", "company_name", "query", "question", "link", "source", "price", "rating", "reviews", "date", "snippet"];
  const MAIN_KEYS = [
    "organic_results", "jobs_results", "shopping_results", "local_results", "places", "news_results", "top_stories", "images_results",
    "video_results", "videos_results", "properties", "best_flights", "other_flights", "reviews", "articles", "profiles", "events_results",
    "inline_shopping_results", "immersive_products", "related_questions", "trending_searches", "suggestions", "visual_matches", "ads",
  ];

  function titleOfItem(item) {
    for (const key of ["title", "name", "company_name", "query", "question", "text", "snippet"]) {
      if (typeof item[key] === "string" && item[key]) return item[key];
    }
    return "";
  }

  /** One-level flatten with dotted keys; arrays of scalars join with ", ", arrays of objects show a count. */
  function flattenObject(obj, { maxDepth = 2, prefix = "" } = {}) {
    const out = {};
    const visit = (value, key, depth) => {
      if (value === null || value === undefined) { out[key] = ""; return; }
      if (Array.isArray(value)) {
        if (value.every((v) => v === null || typeof v !== "object")) out[key] = value.join(", ");
        else {
          const titles = value.map((v) => (isObject(v) ? titleOfItem(v) : "")).filter(Boolean);
          out[key] = titles.length ? titles.slice(0, 3).join(" · ") + (value.length > 3 ? ` (+${value.length - 3})` : "") : `[${value.length} items]`;
        }
        return;
      }
      if (isObject(value)) {
        if (depth >= maxDepth) { out[key] = JSON.stringify(value); return; }
        const keys = Object.keys(value);
        if (!keys.length) { out[key] = ""; return; }
        for (const k of keys) visit(value[k], key ? `${key}.${k}` : k, depth + 1);
        return;
      }
      out[key] = value;
    };
    for (const [k, v] of Object.entries(obj)) visit(v, prefix ? `${prefix}.${k}` : k, 1);
    return out;
  }

  /** Rows and columns for a list of records. Columns: preferred keys first, then by how often they occur. */
  function flattenRows(list, { maxColumns = 14, sample = 300 } = {}) {
    const rows = list.map((item) => (isObject(item) ? flattenObject(item) : { value: item }));
    const counts = new Map();
    const first = new Map();
    rows.slice(0, sample).forEach((row) => {
      for (const key of Object.keys(row)) {
        counts.set(key, (counts.get(key) || 0) + 1);
        if (!first.has(key)) first.set(key, first.size);
      }
    });
    const rank = (key) => {
      const root = key.split(".")[0];
      const index = TABLE_PREFERRED.indexOf(root);
      return index === -1 ? 100 : index;
    };
    const keys = [...counts.keys()].sort((a, b) => rank(a) - rank(b) || counts.get(b) - counts.get(a) || first.get(a) - first.get(b));
    const columns = keys.slice(0, maxColumns).map((key) => ({ key, label: humanize(key) }));
    return { columns, rows, hidden: Math.max(0, keys.length - maxColumns) };
  }

  const csvCell = (value) => {
    let text = value === null || value === undefined ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
    if (/^[=+@\t\r]/.test(text) || /^-(?![\d.])/.test(text)) text = `'${text}`; // neutralise spreadsheet formulas
    return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  function rowsToCsv(columns, rows) {
    return [columns.map((c) => csvCell(c.key)).join(","), ...rows.map((row) => columns.map((c) => csvCell(row[c.key])).join(","))].join("\r\n");
  }

  const isRecordList = (value) => Array.isArray(value) && value.length > 0 && value.some(isObject);

  /** Every list of records worth tabulating: top-level arrays, plus one level inside objects like local_results.places. */
  function listCandidates(result) {
    const found = [];
    if (!isObject(result)) return found;
    for (const [key, value] of Object.entries(result)) {
      if (["search_metadata", "search_parameters"].includes(key)) continue;
      if (isRecordList(value)) found.push({ key, path: [key], list: value });
      else if (isObject(value)) {
        for (const [inner, innerValue] of Object.entries(value)) {
          if (isRecordList(innerValue) && innerValue.length >= 2) found.push({ key: `${key}.${inner}`, path: [key, inner], list: innerValue });
        }
      }
    }
    return found;
  }

  /** The list a table should show first: well-known result keys, else the biggest list of records. */
  function findMainList(result) {
    const candidates = listCandidates(result);
    if (!candidates.length) return null;
    for (const name of MAIN_KEYS) {
      const hit = candidates.find((c) => c.key === name) || candidates.find((c) => c.path[c.path.length - 1] === name);
      if (hit) return hit;
    }
    return [...candidates].sort((a, b) => b.list.length - a.list.length)[0];
  }

  // ---- chart geometry ------------------------------------------------------------------------------------------

  function niceTicks(min, max, count = 4) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
    if (min === max) return [min];
    const span = max - min;
    const rough = span / Math.max(1, count);
    const magnitude = 10 ** Math.floor(Math.log10(rough));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) || 10 * magnitude;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(Math.round(v / step) * step);
    return ticks;
  }

  /**
   * Line geometry for a numeric series in a width x height box. Null values break the line. Returns
   * {line, area, points:[{x,y,v,i}], min, max, yOf}. Bounds default to the data with 6% headroom, or the given ones.
   */
  function seriesGeometry(values, { width, height, padLeft = 0, padRight = 0, padTop = 0, padBottom = 0, min, max, count } = {}) {
    const numeric = values.filter((v) => typeof v === "number" && Number.isFinite(v));
    const n = count || values.length;
    let lo = min !== undefined ? min : numeric.length ? Math.min(...numeric) : 0;
    let hi = max !== undefined ? max : numeric.length ? Math.max(...numeric) : 1;
    if (min === undefined && max === undefined) {
      const room = (hi - lo || Math.abs(hi) || 1) * 0.06;
      lo -= room;
      hi += room;
    } else if (lo === hi) hi = lo + 1;
    const innerW = width - padLeft - padRight;
    const innerH = height - padTop - padBottom;
    const xOf = (i) => padLeft + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
    const yOf = (v) => padTop + innerH - ((v - lo) / (hi - lo)) * innerH;
    const points = [];
    let line = "";
    let area = "";
    let run = [];
    const flush = () => {
      if (!run.length) return;
      line += run.map((p, k) => `${k ? "L" : "M"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join("");
      const base = (padTop + innerH).toFixed(1);
      area += `M${run[0].x.toFixed(1)} ${base}` + run.map((p) => `L${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join("") + `L${run[run.length - 1].x.toFixed(1)} ${base}Z`;
      run = [];
    };
    values.forEach((v, i) => {
      if (typeof v !== "number" || !Number.isFinite(v)) { flush(); return; }
      const point = { x: xOf(i), y: yOf(v), v, i };
      points.push(point);
      run.push(point);
    });
    flush();
    return { line, area, points, min: lo, max: hi, xOf, yOf };
  }

  // ---- values and labels -----------------------------------------------------------------------------------------

  function formatDuration(minutes) {
    const total = Math.round(Number(minutes));
    if (!Number.isFinite(total) || total <= 0) return "";
    const hours = Math.floor(total / 60);
    const rest = total % 60;
    return [hours ? `${hours}h` : "", rest ? `${rest}m` : ""].filter(Boolean).join(" ");
  }

  /** Rating from number, "4.5", [{type,rating,count}] or {rating,count,value}. Returns {value, count} or null. */
  function normalizeRating(raw, fallbackCount) {
    let value = null;
    let count = fallbackCount === undefined ? null : parseNumber(fallbackCount);
    if (Array.isArray(raw)) {
      const entry = raw.find((r) => isObject(r) && (r.rating !== undefined || r.value !== undefined));
      if (entry) { value = parseNumber(entry.rating !== undefined ? entry.rating : entry.value); if (entry.count !== undefined) count = parseNumber(entry.count); }
    } else if (isObject(raw)) {
      value = parseNumber(raw.rating !== undefined ? raw.rating : raw.value !== undefined ? raw.value : raw.score);
      if (raw.count !== undefined || raw.reviews !== undefined) count = parseNumber(raw.count !== undefined ? raw.count : raw.reviews);
    } else value = parseNumber(raw);
    if (value === null || value < 0) return null;
    return { value, count };
  }

  /** Plain text for any scalar-ish field; objects use their name/title. */
  function textOf(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (Array.isArray(value)) return value.map(textOf).filter(Boolean).join(", ");
    if (isObject(value)) return textOf(value.name || value.title || value.text || value.value || value.snippet || "");
    return "";
  }
  const pick = (obj, ...keys) => {
    if (!obj) return undefined;
    for (const key of keys) if (!isEmpty(obj[key])) return obj[key];
    return undefined;
  };

  // ---- visual section plan -----------------------------------------------------------------------------------------

  /** Keys with their own renderer; value is the renderer id. Order here is the display order. */
  const SECTION_RENDERERS = [
    ["ai_overview", "aiOverview"], ["text_blocks", "aiBlocks"], ["markdown", "markdown"], ["reconstructed_markdown", "markdown"],
    ["answer_box", "answerBox"], ["knowledge_graph", "knowledgeGraph"], ["summary", "finance"], ["market_trends", "marketTrends"],
    ["product_results", "product"], ["product_result", "product"], ["place_results", "place"],
    ["interest_over_time", "trendsTimeline"], ["interest_by_region", "regionBars"], ["compared_breakdown_by_region", "regionBars"],
    ["trending_searches", "trendingNow"],
    ["price_insights", "priceInsights"], ["best_flights", "flights"], ["other_flights", "flights"], ["properties", "hotels"],
    ["jobs_results", "jobs"],
    ["organic_results", "organic"], ["articles", "organic"], ["profiles", "organic"],
    ["ads", "organic"], ["ads_results", "organic"], ["sponsored_brands", "organic"],
    ["local_results", "places"], ["places", "places"],
    ["shopping_results", "shopping"], ["inline_shopping_results", "shopping"], ["immersive_products", "shopping"], ["related_products", "shopping"],
    ["top_stories", "news"], ["news_results", "news"],
    ["images_results", "images"], ["inline_images", "images"], ["image_results", "images"], ["visual_matches", "images"], ["photos", "images"],
    ["video_results", "videos"], ["videos_results", "videos"], ["inline_videos", "videos"], ["short_video_results", "videos"], ["shorts_results", "videos"], ["movies_results", "videos"],
    ["events_results", "events"],
    ["ratings", "ratingBars"], ["reviews", "reviews"], ["reviews_results", "reviews"], ["user_reviews", "reviews"],
    ["cited_by", "citedBy"],
    ["related_questions", "questions"],
    ["related_searches", "chips"], ["suggestions", "chips"], ["suggested_searches", "chips"], ["top_searches", "chips"], ["categories", "chips"],
    ["topics", "chips"], ["related_topics", "relatedTrends"], ["related_queries", "relatedTrends"], ["filters", "filters"], ["refine_this_search", "chips"],
    ["transcript", "transcript"], ["chapters", "transcript"],
  ];
  /** Keys shown elsewhere (header strip, footer, or consumed by another section) and never listed on their own. */
  const CONSUMED_KEYS = new Set(["search_metadata", "search_parameters", "search_information", "pagination", "serpapi_pagination"]);

  const countOf = (value) => (Array.isArray(value) ? value.length : null);

  /**
   * Decide which renderer shows each top-level key. Returns [{key, renderer, count}] in display order followed by the
   * keys that fall back to the JSON tree (renderer "tree"). Arrays of titled records not in the table get "cards".
   */
  function planSections(result) {
    if (!isObject(result)) return [];
    const finance = isObject(result.summary) && Array.isArray(result.graph);
    const hidden = new Set(CONSUMED_KEYS);
    if (finance) hidden.add("graph");
    if (Array.isArray(result.text_blocks)) { hidden.add("markdown"); hidden.add("reconstructed_markdown"); }
    if (Array.isArray(result.text_blocks) || isObject(result.ai_overview)) hidden.add("references");
    if (["reviews", "reviews_results", "user_reviews"].some((k) => Array.isArray(result[k]))) hidden.add("place_info");
    if (typeof result.error === "string") hidden.add("error");
    const keys = Object.keys(result).filter((key) => !hidden.has(key) && !isEmpty(result[key]));
    const plan = [];
    const placed = new Set();
    for (const [key, renderer] of SECTION_RENDERERS) {
      if (!keys.includes(key)) continue;
      if (key === "summary" && !finance) continue;
      if (key === "ratings" && !isRecordList(result.ratings)) continue;
      if (key === "filters" && !isRecordList(result.filters)) continue;
      if (["related_searches", "suggestions", "suggested_searches", "top_searches", "categories", "topics", "refine_this_search"].includes(key) && !Array.isArray(result[key])) continue;
      plan.push({ key, renderer, count: countOf(result[key]) });
      placed.add(key);
    }
    const order = new Map(keys.map((key, index) => [key, index]));
    const rest = keys.filter((key) => !placed.has(key));
    for (const key of rest.sort((a, b) => order.get(a) - order.get(b))) {
      const value = result[key];
      const titled = isRecordList(value) && value.slice(0, 5).filter(isObject).every((item) => titleOfItem(item));
      plan.push({ key, renderer: titled ? "cards" : "tree", count: countOf(value) });
    }
    return plan;
  }

  const logic = {
    LANGUAGES, LANGUAGE_LABELS, GROUP_ORDER, isEmpty, humanize, parseNumber, safeLink, safeImage,
    dateFormatOf, normalizeDate, coerceValue, validateParams, conflictPairs, anyOfGroups, orderedGroups,
    paramPairs, endpointUrl, buildSnippets, requestUrl, tokenize, pathToString, searchTree,
    flattenObject, flattenRows, rowsToCsv, listCandidates, findMainList,
    niceTicks, seriesGeometry, formatDuration, normalizeRating, textOf, pick, planSections, titleOfItem,
  };

  // ===========================================================================
  // DOM building blocks (browser only from here on; nothing below runs at load)
  // ===========================================================================

  const SVG_NS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs = {}, ...children) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "text") node.textContent = value;
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) if (child) node.append(child);
    return node;
  }

  // Trusted static icon markup (the only innerHTML in this file; there is no user data in it).
  const ICON_PATHS = {
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    download: '<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>',
    pin: '<path d="M12 17v5M8 3h8l-1 6 3 4H6l3-4z"/>',
    expand: '<path d="m7 9 5 5 5-5"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1"/>',
    map: '<path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    plane: '<path d="M10.5 13.5 3 11l1-2 8 1.2L16 5l2 1-2.4 5.5 4.4 1.2-.5 1.8-5-.6-1.9 4.6-1.7-.7z"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5.5-5.5L5 20"/>',
    play: '<path d="M8 5.5v13l11-6.5z"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    warn: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v5M12 17.5v.5"/>',
  };
  function icon(name, { size } = {}) {
    const node = h("span", { class: "sk-icon", "aria-hidden": "true", html: `<svg viewBox="0 0 24 24" focusable="false">${ICON_PATHS[name] || ""}</svg>` });
    if (size) { node.style.width = `${size}px`; node.style.height = `${size}px`; }
    return node;
  }

  let uidCounter = 0;
  const uid = (prefix = "sk") => `${prefix}${(uidCounter += 1).toString(36)}${Math.random().toString(36).slice(2, 5)}`;
  const debounce = (fn, ms) => {
    let timer = null;
    const wrapped = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
    wrapped.cancel = () => clearTimeout(timer);
    return wrapped;
  };

  async function copyText(text, label = "Copied") {
    const value = String(text);
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(value);
      else throw new Error("no clipboard api");
    } catch {
      const area = h("textarea", { "aria-hidden": "true", tabindex: "-1", style: { position: "fixed", left: "-9999px", top: "0" } });
      area.value = value;
      document.body.append(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      area.remove();
      if (!ok) { Aira.toast("Copy is blocked in this browser. Select the text and copy it manually.", "error"); return false; }
    }
    if (label) Aira.toast(label, "success", { timeout: 1800 });
    return true;
  }

  /** Small copy button that flips to a check mark for a moment. `source` is a string or a function returning one. */
  function copyButton(source, { label = "Copy", message = "Copied", sm = true, text = false } = {}) {
    const button = h("button", { type: "button", class: `btn secondary${sm ? " sm" : ""} sk-copy`, "aria-label": label, title: label },
      icon("copy"), text ? h("span", { text: label }) : null);
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const ok = await copyText(typeof source === "function" ? source() : source, message);
      if (!ok) return;
      button.classList.add("done");
      button.replaceChildren(...[icon("check"), text ? h("span", { text: "Copied" }) : null].filter(Boolean));
      setTimeout(() => { button.classList.remove("done"); button.replaceChildren(...[icon("copy"), text ? h("span", { text: label }) : null].filter(Boolean)); }, 1400);
    });
    return button;
  }

  function downloadText(filename, text, type = "text/plain") {
    const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
    const link = h("a", { href: url, download: filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  // ---- small display atoms -------------------------------------------------------------------------------------------

  const fmtNumber = (n) => (typeof n === "number" ? n.toLocaleString() : String(n));
  const compactNumber = (n) => {
    const value = Number(n);
    if (!Number.isFinite(value)) return String(n);
    return new Intl.NumberFormat([], { notation: "compact", maximumFractionDigits: 1 }).format(value);
  };

  /** External link that is only live for http(s) URLs. */
  function extLink(url, children, attrs = {}) {
    const safe = safeLink(url);
    if (!safe) return h("span", attrs, children);
    return h("a", { ...attrs, href: safe, target: "_blank", rel: "noopener noreferrer" }, children);
  }
  const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } };

  /** Remote images show only for https (or inline data) and degrade to a labelled placeholder when blocked. */
  function thumb(source, { alt = "", cls = "", fallback = "" } = {}) {
    const src = safeImage(source);
    const box = h("span", { class: `sk-thumb ${cls}`.trim() });
    const placeholder = () => { box.classList.add("is-ph"); box.replaceChildren(h("span", { class: "sk-thumb-ph", "aria-hidden": "true", text: (fallback || alt || "?").trim().charAt(0).toUpperCase() })); };
    if (!src) { placeholder(); return box; }
    const img = h("img", { src, alt, loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
    img.addEventListener("error", placeholder, { once: true });
    box.append(img);
    return box;
  }
  const imageField = (item, ...keys) => {
    for (const key of [...keys, "thumbnail", "image", "favicon", "logo", "icon", "original", "thumbnail_url"]) {
      const value = item && item[key];
      if (typeof value === "string" && value) return value;
      if (isObject(value)) { const inner = value.static || value.url || value.link || value.src || value.original || value.thumbnail; if (typeof inner === "string") return inner; }
      if (Array.isArray(value) && value.length) {
        const first = value[0];
        const inner = typeof first === "string" ? first : first && (first.link || first.url || first.thumbnail || first.original_image || first.image);
        if (typeof inner === "string") return inner;
      }
    }
    return "";
  };

  /** ★★★★☆ with a clipped fill for fractions. */
  function stars(value, { count, max = 5 } = {}) {
    const rating = normalizeRating(value, count);
    if (!rating) return null;
    const pct = clamp((rating.value / max) * 100, 0, 100);
    const label = `${rating.value} out of ${max}${rating.count ? `, ${fmtNumber(rating.count)} reviews` : ""}`;
    return h("span", { class: "sk-rating", role: "img", "aria-label": label, title: label },
      h("span", { class: "sk-stars", "aria-hidden": "true" }, h("span", { class: "sk-stars-fill", style: { width: `${pct}%` }, text: "★★★★★" })),
      h("span", { class: "sk-rating-num", "aria-hidden": "true", text: Number.isInteger(rating.value) ? rating.value.toFixed(1) : String(rating.value) }),
      rating.count ? h("span", { class: "sk-rating-count", "aria-hidden": "true", text: `(${compactNumber(rating.count)})` }) : null);
  }

  const chip = (text, tone = "") => (isEmpty(text) ? null : h("span", { class: `sk-chip ${tone}`.trim(), text: String(text) }));
  const chipRow = (items, tone = "") => {
    const nodes = items.map((item) => (item instanceof Node ? item : chip(item, tone))).filter(Boolean);
    return nodes.length ? h("div", { class: "sk-chips" }, nodes) : null;
  };
  const metaLine = (...parts) => {
    const nodes = parts.flat(Infinity).filter((p) => p !== null && p !== undefined && p !== "" && p !== false);
    if (!nodes.length) return null;
    return h("div", { class: "sk-meta" }, nodes.map((p, i) => [i ? h("span", { class: "sk-dot", "aria-hidden": "true", text: "·" }) : null, p instanceof Node ? p : h("span", { text: String(p) })]));
  };
  const note = (text, tone = "") => h("div", { class: `sk-note ${tone}`.trim(), role: tone === "warn" || tone === "error" ? "alert" : "status" }, icon(tone === "warn" || tone === "error" ? "warn" : "info"), h("span", { text }));

  /** Bold the matched words inside a snippet using text nodes only. */
  function highlighted(text, words) {
    const list = (Array.isArray(words) ? words : []).filter((w) => typeof w === "string" && w.length > 1).slice(0, 12);
    const value = String(text || "");
    if (!list.length) return [value];
    const pattern = new RegExp(`(${list.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
    return value.split(pattern).map((part, index) => (index % 2 ? h("mark", { text: part }) : part));
  }

  /** Collapsible titled section used by the visual renderers and the metadata view. */
  function section(title, { count, id, open = true, body, aside, tone = "" } = {}) {
    const bodyId = uid("sec");
    const toggle = h("button", { type: "button", class: "sk-sec-toggle", "aria-expanded": String(open), "aria-controls": bodyId },
      icon("chevron"), h("h3", { text: title }), count !== undefined && count !== null ? h("span", { class: "sk-count", text: String(count) }) : null);
    const content = h("div", { class: "sk-sec-body", id: bodyId, hidden: !open }, body);
    toggle.addEventListener("click", () => {
      const next = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(next));
      content.hidden = !next;
    });
    return h("section", { class: `sk-sec ${tone}`.trim(), id: id || null, "aria-label": title },
      h("header", { class: "sk-sec-head" }, toggle, aside ? h("div", { class: "sk-sec-aside" }, aside) : null), content);
  }

  // ---- generic controls ---------------------------------------------------------------------------------------

  /** Roving arrow-key behaviour for a set of buttons that act like radios or tabs. */
  function rovingKeys(container, selector) {
    container.addEventListener("keydown", (event) => {
      const keys = ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"];
      if (!keys.includes(event.key)) return;
      const items = [...container.querySelectorAll(selector)].filter((node) => !node.disabled);
      const index = items.indexOf(document.activeElement);
      if (index === -1) return;
      event.preventDefault();
      let next = index;
      if (event.key === "Home") next = 0;
      else if (event.key === "End") next = items.length - 1;
      else next = (index + (event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next].focus();
      if (container.dataset.roving === "select") items[next].click();
    });
  }

  /** Radio-style segmented control. options: [[value, label]]. Returns {node, get, set}. */
  function segmented(options, value, onChange, { label, id } = {}) {
    let current = value === undefined || value === null ? "" : String(value);
    const group = h("div", { class: "sk-seg", role: "radiogroup", "aria-label": label || null, id: id || null, dataset: { roving: "select" } });
    const buttons = options.map(([val, text]) => {
      const button = h("button", { type: "button", role: "radio", class: "sk-seg-btn", dataset: { value: String(val) }, text });
      button.addEventListener("click", () => { if (current === String(val)) return; current = String(val); sync(); onChange && onChange(current); });
      return button;
    });
    const sync = () => {
      const known = buttons.some((b) => b.dataset.value === current);
      buttons.forEach((button) => {
        const on = known ? button.dataset.value === current : button === buttons[0];
        button.setAttribute("aria-checked", String(on));
        button.tabIndex = on ? 0 : -1;
      });
    };
    group.append(...buttons);
    rovingKeys(group, ".sk-seg-btn");
    sync();
    return { node: group, get: () => current, set: (next) => { current = next === undefined || next === null ? "" : String(next); sync(); } };
  }

  /** Labelled on/off switch. Returns {node, input, get, set}. */
  function toggleSwitch(text, checked, onChange, { hint, id } = {}) {
    const input = h("input", { type: "checkbox", role: "switch", id: id || uid("sw"), checked: !!checked });
    input.addEventListener("change", () => onChange && onChange(input.checked));
    const node = h("label", { class: "sk-switch" }, input, h("span", { class: "sk-track", "aria-hidden": "true" }, h("i")),
      h("span", { class: "sk-switch-text" }, h("span", { text }), hint ? h("small", { text: hint }) : null));
    return { node, input, get: () => input.checked, set: (next) => { input.checked = !!next; } };
  }

  /** Free-text chip input (tags). options optional for datalist suggestions. Returns {node, input, get, set}. */
  function tagInput(initial, onChange, { options = [], id, placeholder = "Type a value and press Enter" } = {}) {
    let items = [];
    const input = h("input", { type: "text", id: id || uid("tg"), autocomplete: "off", placeholder, class: "sk-tag-input", "aria-label": placeholder });
    const wrap = h("div", { class: "sk-tags" });
    let list = null;
    if (options.length) {
      list = h("datalist", { id: uid("dl") }, options.map((o) => h("option", { value: String(o.value), label: o.label })));
      input.setAttribute("list", list.id);
    }
    const render = () => {
      wrap.replaceChildren(...items.map((item, index) => h("span", { class: "sk-tag" }, h("span", { text: item }),
        h("button", { type: "button", "aria-label": `Remove ${item}`, tabindex: "-1", onclick: () => { items.splice(index, 1); render(); onChange && onChange(); input.focus(); } }, icon("close")))), input, ...(list ? [list] : []));
    };
    const commit = () => {
      const parts = input.value.split(",").map((s) => s.trim()).filter(Boolean);
      if (!parts.length) return false;
      for (const part of parts) if (!items.includes(part)) items.push(part);
      input.value = "";
      render();
      onChange && onChange();
      input.focus();
      return true;
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === ",") { if (input.value.trim()) { event.preventDefault(); event.stopPropagation(); commit(); } }
      else if (event.key === "Backspace" && !input.value && items.length) { items.pop(); render(); onChange && onChange(); input.focus(); }
    });
    input.addEventListener("blur", () => { commit(); });
    input.addEventListener("paste", (event) => {
      const text = (event.clipboardData || {}).getData ? event.clipboardData.getData("text") : "";
      if (text.includes(",")) { event.preventDefault(); input.value = text; commit(); }
    });
    wrap.addEventListener("click", (event) => { if (event.target === wrap) input.focus(); });
    const set = (value) => {
      items = (Array.isArray(value) ? value : String(value || "").split(",")).map((s) => String(s).trim()).filter(Boolean);
      render();
    };
    set(initial);
    return { node: wrap, input, get: () => { const extra = input.value.trim(); return [...items, ...(extra ? [extra] : [])]; }, set };
  }

  // ---- location picker ---------------------------------------------------------------------------------------------

  const locationCache = new Map();
  async function fetchLocations(query, limit) {
    const key = `${limit}:${query.toLowerCase()}`;
    if (locationCache.has(key)) return locationCache.get(key);
    const data = await Aira.api(`/api/airaapi/locations?${new URLSearchParams({ q: query, limit: String(limit) })}`, { silent: true });
    const items = Array.isArray(data.locations) ? data.locations : [];
    if (locationCache.size > 120) locationCache.delete(locationCache.keys().next().value);
    locationCache.set(key, items);
    return items;
  }

  /**
   * Combobox bound to /api/airaapi/locations. Typing is debounced (250 ms), results arrive as a listbox, and the
   * typed text stays valid even if nothing is picked (engines accept any string; canonical names just work best).
   * Returns the wrapper node; node.input, node.get(), node.set(text) are available to forms.
   */
  function locationPicker({ value = "", onPick, onInput, id, placeholder = "City, region or country", limit = 8 } = {}) {
    const listId = uid("lb");
    const input = h("input", {
      type: "text", id: id || uid("loc"), role: "combobox", autocomplete: "off", spellcheck: "false", placeholder, value,
      "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": listId, "aria-haspopup": "listbox",
    });
    const list = h("ul", { class: "sk-pop", id: listId, role: "listbox", "aria-label": "Matching locations", hidden: true });
    const live = h("div", { class: "visually-hidden", role: "status", "aria-live": "polite" });
    const wrap = h("div", { class: "sk-loc" }, input, list, live);
    let items = [];
    let active = -1;
    let sequence = 0;

    const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); active = -1; };
    const open = () => { list.hidden = false; input.setAttribute("aria-expanded", "true"); };
    const setActive = (index) => {
      active = index;
      [...list.children].forEach((li, i) => { li.setAttribute("aria-selected", String(i === index)); if (i === index) { input.setAttribute("aria-activedescendant", li.id); li.scrollIntoView({ block: "nearest" }); } });
      if (index < 0) input.removeAttribute("aria-activedescendant");
    };
    const choose = (location) => {
      input.value = location.canonical_name || location.name || "";
      close();
      onPick && onPick(location);
      onInput && onInput(input.value);
    };
    const showMessage = (text, retry) => {
      list.replaceChildren(h("li", { class: "sk-pop-msg", role: "presentation" }, h("span", { text }),
        retry ? h("button", { type: "button", class: "btn secondary sm", text: "Try again", onmousedown: (e) => e.preventDefault(), onclick: retry }) : null));
      open();
    };
    const run = async () => {
      const query = input.value.trim();
      if (query.length < 2) { close(); return; }
      const mine = (sequence += 1);
      showMessage("Searching locations…");
      try {
        const found = await fetchLocations(query, limit);
        if (mine !== sequence) return;
        items = found;
        if (!items.length) { showMessage("No match. The text you typed is sent as it is."); live.textContent = "No matching locations"; return; }
        list.replaceChildren(...items.map((location, index) => {
          const li = h("li", { role: "option", id: `${listId}-${index}`, "aria-selected": "false", class: "sk-opt" },
            h("span", { class: "sk-opt-name", text: location.canonical_name || location.name }),
            h("span", { class: "sk-opt-meta", text: [location.target_type, location.country_code, location.reach ? `${compactNumber(location.reach)} reach` : ""].filter(Boolean).join(" · ") }));
          li.addEventListener("mousedown", (event) => { event.preventDefault(); choose(location); });
          li.addEventListener("mousemove", () => { if (active !== index) setActive(index); });
          return li;
        }));
        open();
        live.textContent = `${items.length} locations. Use the arrow keys to choose.`;
        setActive(-1);
      } catch (error) {
        if (mine !== sequence) return;
        showMessage(error && error.message ? error.message : "Locations could not be loaded.", run);
      }
    };
    const debounced = debounce(run, 250);
    input.addEventListener("input", () => { onInput && onInput(input.value); debounced(); });
    input.addEventListener("focus", () => { if (items.length && input.value.trim().length >= 2 && list.children.length) open(); });
    input.addEventListener("blur", () => setTimeout(close, 120));
    input.addEventListener("keydown", (event) => {
      const isOpen = !list.hidden && items.length > 0 && list.querySelector("[role=option]");
      if (event.key === "ArrowDown") { event.preventDefault(); if (!isOpen) { if (input.value.trim().length >= 2) run(); return; } setActive((active + 1) % items.length); }
      else if (event.key === "ArrowUp" && isOpen) { event.preventDefault(); setActive((active - 1 + items.length) % items.length); }
      else if (event.key === "Enter" && isOpen && active >= 0) { event.preventDefault(); event.stopPropagation(); choose(items[active]); }
      else if (event.key === "Escape" && !list.hidden) { event.preventDefault(); event.stopPropagation(); close(); }
    });
    wrap.input = input;
    wrap.get = () => input.value.trim();
    wrap.set = (text) => { sequence += 1; input.value = text === undefined || text === null ? "" : String(text); close(); };
    return wrap;
  }

  // ---- parameter form -----------------------------------------------------------------------------------------------

  const WIDE_TYPES = new Set(["csv"]);
  const isJsonParam = (param) => /_json$|^data$/.test(param.name) || /\bJSON\b/.test(param.help || "");

  function buildControl(entry, param, id, changed) {
    const options = Array.isArray(param.options) ? param.options : [];
    const closed = closedOptions(param);
    const label = param.label || param.name;
    switch (param.type) {
      case "enum": {
        if (closed && options.length <= 4 && options.reduce((n, o) => n + String(o.label || o.value).length, 0) <= 44) {
          const control = segmented([["", "Default"], ...options.map((o) => [String(o.value), o.label || String(o.value)])], param.default, () => changed(), { label, id });
          return { node: control.node, get: control.get, set: control.set, focusEl: control.node.querySelector("[tabindex='0']") || control.node, group: true };
        }
        if (closed) {
          const select = h("select", { id }, h("option", { value: "", text: param.default ? `Default (${param.default})` : "Default" }),
            options.map((o) => h("option", { value: String(o.value), text: o.label && o.label !== String(o.value) ? `${o.label} (${o.value})` : String(o.value) })));
          select.addEventListener("change", () => changed());
          return { node: select, get: () => select.value, set: (v) => {
            const wanted = isEmpty(v) ? "" : String(v);
            if (wanted && ![...select.options].some((o) => o.value === wanted)) select.append(h("option", { value: wanted, text: `${wanted} (custom)` }));
            select.value = wanted;
          }, focusEl: select };
        }
        const list = h("datalist", { id: `${id}-dl` }, options.map((o) => h("option", { value: String(o.value), label: o.label })));
        const input = h("input", { type: "text", id, list: options.length ? list.id : null, autocomplete: "off", placeholder: param.default ? `Default: ${param.default}` : "Any value" });
        input.addEventListener("input", () => changed());
        return { node: h("div", { class: "sk-stack" }, input, list), get: () => input.value, set: (v) => { input.value = isEmpty(v) ? "" : String(v); }, focusEl: input };
      }
      case "boolean": {
        const defaultOn = param.default === true;
        const sw = toggleSwitch("Enabled", defaultOn, () => changed(), { id, hint: `Default: ${defaultOn ? "on" : "off"}` });
        const set = (v) => {
          const on = v === true || v === "true" || v === 1 || v === "1";
          const off = v === false || v === "false" || v === 0 || v === "0";
          sw.set(defaultOn ? !off : on);
        };
        return { node: sw.node, get: () => (defaultOn ? (sw.get() ? "" : false) : sw.get() ? true : ""), set, focusEl: sw.input };
      }
      case "integer":
      case "number": {
        const pagination = entry.pagination || {};
        const pageStep = pagination.param === param.name && pagination.page_size ? pagination.page_size : null;
        const fine = /^(lat|lon|lng)/.test(param.name);
        const step = pageStep || (fine ? 0.01 : 1);
        const floor = pageStep ? 0 : null;
        const input = h("input", { type: "number", id, inputmode: param.type === "integer" ? "numeric" : "decimal", step: param.type === "integer" ? "1" : "any", placeholder: !isEmpty(param.default) ? `Default: ${param.default}` : "", min: floor !== null ? "0" : null });
        input.addEventListener("input", () => changed());
        const bump = (direction) => {
          const base = Number.parseFloat(input.value);
          let next = (Number.isFinite(base) ? base : Number(param.default) || 0) + direction * step;
          if (floor !== null) next = Math.max(floor, next);
          input.value = String(Math.round(next * 1e6) / 1e6);
          changed();
        };
        const node = h("div", { class: "sk-stepper" },
          h("button", { type: "button", class: "btn secondary icon", "aria-label": `Decrease ${label}`, tabindex: "-1", onclick: () => bump(-1) }, icon("minus")), input,
          h("button", { type: "button", class: "btn secondary icon", "aria-label": `Increase ${label}`, tabindex: "-1", onclick: () => bump(1) }, icon("plus")));
        return { node, get: () => input.value, set: (v) => { input.value = isEmpty(v) ? "" : String(v); }, focusEl: input };
      }
      case "csv": {
        if (closed) {
          let selected = new Set();
          const buttons = options.map((o) => {
            const button = h("button", { type: "button", class: "sk-pill", "aria-pressed": "false", dataset: { value: String(o.value) }, text: o.label || String(o.value) });
            button.addEventListener("click", () => { const v = String(o.value); if (selected.has(v)) selected.delete(v); else selected.add(v); paint(); changed(); });
            return button;
          });
          const paint = () => buttons.forEach((b) => b.setAttribute("aria-pressed", String(selected.has(b.dataset.value))));
          const node = h("div", { class: "sk-pills", role: "group", "aria-label": label, id }, buttons);
          return { node, get: () => buttons.filter((b) => selected.has(b.dataset.value)).map((b) => b.dataset.value), set: (v) => { selected = new Set((Array.isArray(v) ? v : String(v || "").split(",")).map((s) => String(s).trim()).filter(Boolean)); paint(); }, focusEl: buttons[0] || node, group: true };
        }
        const tags = tagInput([], () => changed(), { options, id, placeholder: param.suggestions ? "Add a value" : "Add a value, press Enter" });
        return { node: tags.node, get: tags.get, set: tags.set, focusEl: tags.input };
      }
      case "date": {
        const input = h("input", { type: "date", id });
        input.addEventListener("input", () => changed());
        const toInput = (v) => { const iso = normalizeDate(v, "iso"); return iso || ""; };
        return { node: input, get: () => (input.value ? normalizeDate(input.value, dateFormatOf(param)) || "" : ""), set: (v) => { input.value = isEmpty(v) ? "" : toInput(v); }, focusEl: input };
      }
      case "location": {
        const picker = locationPicker({ id, onInput: () => changed() });
        return { node: picker, get: picker.get, set: picker.set, focusEl: picker.input };
      }
      default: {
        const multiline = isJsonParam(param);
        const input = multiline
          ? h("textarea", { id, rows: "3", spellcheck: "false", class: "sk-mono", placeholder: !isEmpty(param.default) ? `Default: ${param.default}` : "JSON" })
          : h("input", { type: "text", id, autocomplete: "off", spellcheck: "false", placeholder: !isEmpty(param.default) ? `Default: ${param.default}` : "" });
        input.addEventListener("input", () => changed());
        const parts = [input];
        if (Array.isArray(param.suggestions) && param.suggestions.length) {
          parts.push(h("div", { class: "sk-suggest", role: "group", "aria-label": `${label} suggestions` }, param.suggestions.map((s) => h("button", {
            type: "button", class: "sk-pill", title: s.value, text: s.label || s.value, onclick: () => { input.value = s.value; changed(); input.focus(); },
          }))));
        }
        return { node: parts.length > 1 ? h("div", { class: "sk-stack" }, parts) : input, get: () => input.value, set: (v) => { input.value = isEmpty(v) ? "" : typeof v === "object" ? JSON.stringify(v) : String(v); }, focusEl: input, wide: multiline };
      }
    }
  }

  /**
   * Catalog-driven parameter form. `entry` is a catalog engine (GET /api/airaapi/engines/{id}).
   * Returns {node, getValues(), validate(), setValues(obj), reset(), focusFirst(), getWarnings()}.
   * getValues() returns clean, typed, non-empty params (unknown keys pass through). validate() shows inline errors,
   * focuses the first one and returns the messages. setValues() never fires onChange. Pressing Enter in a text field
   * calls onSubmit (and dispatches a bubbling "sk-submit" event on the node); Ctrl+Enter does the same anywhere.
   */
  function paramForm(entry, initial = {}, { onChange, onSubmit, compact = false } = {}) {
    const params = entry.params || [];
    const anyOf = anyOfGroups(entry);
    const fields = new Map();
    let extras = {};
    const touched = new Set();
    const node = h("div", { class: `sk-form${compact ? " compact" : ""}`, role: "group", "aria-label": `${entry.name || entry.id} parameters` });
    const need = h("div", { class: "sk-need", hidden: true, "aria-live": "polite" });
    const warnings = h("div", { class: "sk-warnings", "aria-live": "polite" });
    const problems = h("div", { class: "sk-problems", role: "alert" });
    const extrasBox = h("div", { class: "sk-extras", hidden: true });
    const groupNodes = new Map();

    const rawValues = () => {
      const raw = {};
      for (const [name, field] of fields) raw[name] = field.control.get();
      return raw;
    };
    const getValues = () => {
      const { params: clean } = validateParams(entry, rawValues());
      const merged = { ...clean };
      for (const [k, v] of Object.entries(extras)) if (!isEmpty(v)) merged[k] = v;
      return merged;
    };
    const isSet = (name) => { const field = fields.get(name); if (!field) return !isEmpty(extras[name]); return !isEmpty(field.control.get()); };

    // requirement summary ---------------------------------------------------------------------------
    const reqs = [];
    const covered = new Set();
    const alternatives = new Set();
    for (const group of entry.requires_any_of || []) { reqs.push({ names: group }); group.forEach((n) => { covered.add(n); if (group.length > 1) alternatives.add(n); }); }
    for (const p of params) if (p.required && !covered.has(p.name)) reqs.push({ names: [p.name] });
    const labelOf = (name) => (params.find((p) => p.name === name) || {}).label || name;
    const refreshNeed = () => {
      need.hidden = reqs.length === 0;
      need.replaceChildren(...(reqs.length ? [h("span", { class: "sk-need-title", text: "Needs" })] : []), ...reqs.map((req) => {
        const ok = req.names.some(isSet);
        return h("span", { class: `sk-need-item${ok ? " ok" : ""}` }, icon(ok ? "check" : "info"),
          h("span", { text: req.names.length === 1 ? labelOf(req.names[0]) : `One of ${req.names.map(labelOf).join(" / ")}` }), h("span", { class: "visually-hidden", text: ok ? " (provided)" : " (missing)" }));
      }));
    };
    const refreshWarnings = () => {
      const { warnings: list } = validateParams(entry, rawValues());
      warnings.replaceChildren(...list.map((w) => note(w.message, "warn")));
    };
    const refreshGroups = () => {
      for (const [name, g] of groupNodes) {
        const n = g.names.filter(isSet).length;
        g.count.textContent = n ? `${n} set` : "";
        g.count.hidden = !n;
      }
    };
    const refreshExtras = () => {
      const entries = Object.entries(extras).filter(([, v]) => !isEmpty(v));
      extrasBox.hidden = entries.length === 0;
      extrasBox.replaceChildren(h("strong", { text: "Other parameters" }), h("span", { class: "sk-hint", text: " sent as they are" }),
        h("div", { class: "sk-chips" }, entries.map(([k, v]) => h("span", { class: "sk-tag" }, h("code", { text: `${k}=${typeof v === "object" ? JSON.stringify(v) : v}` }),
          h("button", { type: "button", "aria-label": `Remove ${k}`, onclick: () => { delete extras[k]; refreshExtras(); emit(); } }, icon("close"))))));
    };

    // fields ------------------------------------------------------------------------------------------
    const showError = (field, message) => {
      field.error.textContent = message || "";
      field.error.hidden = !message;
      field.control.focusEl.setAttribute("aria-invalid", message ? "true" : "false");
      if (!message) field.control.focusEl.removeAttribute("aria-invalid");
      field.node.classList.toggle("invalid", !!message);
    };
    const checkField = (name) => {
      const field = fields.get(name);
      const raw = field.control.get();
      const { error } = coerceValue(field.param, raw);
      let message = error || "";
      if (!message && (field.param.required || covered.has(name)) && !alternatives.has(name) && (isEmpty(raw) || raw === false)) message = `${field.param.label || name} is required.`;
      showError(field, message);
      return message;
    };
    const emit = () => { problems.replaceChildren(); refreshNeed(); refreshWarnings(); refreshGroups(); if (onChange) onChange(getValues()); };

    const makeField = (param, index) => {
      const id = uid("p");
      const hintId = `${id}-h`;
      const errId = `${id}-e`;
      const state = { param };
      const changed = () => {
        if (touched.has(param.name) || state.node.classList.contains("invalid")) checkField(param.name);
        state.clear.hidden = !isSet(param.name);
        emit();
      };
      const control = buildControl(entry, param, id, changed);
      const labelId = `${id}-l`;
      const groupLike = !!control.group;
      const label = groupLike ? h("span", { class: "sk-label", id: labelId }) : h("label", { for: id, class: "sk-label", id: labelId });
      label.append(param.label || param.name);
      if (groupLike) control.node.setAttribute("aria-labelledby", labelId);
      const help = (param.help || "").trim();
      const defaultText = !isEmpty(param.default) && param.type !== "boolean" ? ` Default: ${param.default}.` : "";
      const hint = h("div", { class: "sk-hint", id: hintId, text: (help + defaultText).trim() || null });
      const helpButton = compact && help ? h("button", { type: "button", class: "sk-help-btn", "aria-label": `About ${param.label || param.name}`, "aria-expanded": "false", "aria-controls": hintId, text: "?" }) : null;
      if (helpButton) { hint.hidden = true; helpButton.addEventListener("click", () => { hint.hidden = !hint.hidden; helpButton.setAttribute("aria-expanded", String(!hint.hidden)); }); }
      const error = h("div", { class: "sk-err", id: errId, role: "alert", hidden: true });
      const clear = h("button", { type: "button", class: "sk-clear", "aria-label": `Clear ${param.label || param.name}`, hidden: true, onclick: () => { control.set(""); changed(); control.focusEl.focus && control.focusEl.focus(); } }, icon("close"));
      const required = param.required || (entry.requires_any_of || []).some((g) => g.length === 1 && g[0] === param.name);
      const oneOf = anyOf[param.name];
      const head = h("div", { class: "sk-field-head" }, label,
        required ? h("span", { class: "sk-req", title: "Required", "aria-hidden": "true", text: "*" }) : null,
        required ? h("span", { class: "visually-hidden", text: "required" }) : null,
        oneOf ? h("span", { class: "sk-oneof", title: `Provide at least one of: ${oneOf[0].map(labelOf).join(", ")}`, text: "one of" }) : null,
        h("code", { class: "sk-pname", text: param.name }), helpButton, clear);
      const wide = WIDE_TYPES.has(param.type) || control.wide || (index === 0 && (param.group || "Search") === "Search");
      state.node = h("div", { class: `sk-field${wide ? " wide" : ""}`, dataset: { name: param.name, type: param.type } }, head, control.node, hint, error);
      const describe = [hintId, errId].join(" ");
      control.focusEl.setAttribute("aria-describedby", describe);
      if (required) control.focusEl.setAttribute("aria-required", "true");
      state.control = control;
      state.error = error;
      state.clear = clear;
      state.search = `${param.name} ${param.label || ""} ${help}`.toLowerCase();
      state.node.addEventListener("focusout", (event) => {
        if (state.node.contains(event.relatedTarget)) return;
        touched.add(param.name);
        checkField(param.name);
        clear.hidden = !isSet(param.name);
      });
      fields.set(param.name, state);
      return state;
    };

    // groups ---------------------------------------------------------------------------------------------
    const groupNames = orderedGroups(entry);
    const firstGroupOf = (p) => p.group || "Search";
    const openBefore = new Map();
    for (const groupName of groupNames) {
      const members = params.filter((p) => firstGroupOf(p) === groupName);
      const nodes = members.map((p, i) => makeField(p, i).node);
      const count = h("span", { class: "sk-group-count", hidden: true });
      const hasRequired = members.some((p) => p.required || covered.has(p.name));
      const openDefault = groupName === "Search" || hasRequired || (!compact && ["Localization", "Filters"].includes(groupName));
      const details = h("details", { class: "sk-group", open: openDefault, dataset: { group: groupName } },
        h("summary", {}, icon("chevron"), h("span", { class: "sk-group-name", text: groupName }), h("span", { class: "sk-group-n", text: `${members.length}` }), count, hasRequired ? h("span", { class: "sk-group-req", text: "required" }) : null),
        h("div", { class: "sk-grid" }, nodes));
      groupNodes.set(groupName, { details, count, names: members.map((p) => p.name), members });
    }

    // filter box --------------------------------------------------------------------------------------------
    const filter = h("input", { type: "search", class: "sk-filter", placeholder: "Filter parameters", "aria-label": "Filter parameters", autocomplete: "off" });
    const applyFilter = () => {
      const text = filter.value.trim().toLowerCase();
      for (const [name, field] of fields) field.node.hidden = !!text && !field.search.includes(text);
      for (const [name, g] of groupNodes) {
        const anyVisible = g.names.some((n) => !fields.get(n).node.hidden);
        g.details.hidden = !anyVisible;
        if (text) { if (!openBefore.has(name)) openBefore.set(name, g.details.open); if (anyVisible) g.details.open = true; }
        else if (openBefore.has(name)) { g.details.open = openBefore.get(name); }
      }
      if (!text) openBefore.clear();
    };
    filter.addEventListener("input", applyFilter);
    const showFilter = !compact && params.length > 10;

    node.append(...[problems, need, showFilter ? h("div", { class: "sk-filter-row" }, icon("search"), filter) : null, warnings, ...[...groupNodes.values()].map((g) => g.details), extrasBox].filter(Boolean));
    node.addEventListener("keydown", (event) => {
      const tag = (event.target.tagName || "").toLowerCase();
      const submitKey = (event.key === "Enter" && (event.ctrlKey || event.metaKey)) || (event.key === "Enter" && tag === "input" && event.target.type !== "search" && !event.defaultPrevented && event.target.getAttribute("role") !== "combobox");
      if (!submitKey) return;
      event.preventDefault();
      node.dispatchEvent(new CustomEvent("sk-submit", { bubbles: true }));
      if (onSubmit) onSubmit();
    });

    // public api -------------------------------------------------------------------------------------------
    const setValues = (values) => {
      const input = values || {};
      extras = {};
      for (const [key, value] of Object.entries(input)) if (!fields.has(key) && !isEmpty(value) && key !== "engine" && key !== "api_key") extras[key] = value;
      for (const [name, field] of fields) {
        const value = input[name];
        field.control.set(isEmpty(value) ? "" : Array.isArray(value) && field.param.type !== "csv" ? value.join(",") : value);
        showError(field, "");
        field.clear.hidden = !isSet(name);
      }
      touched.clear();
      for (const [, g] of groupNodes) if (g.names.some(isSet)) g.details.open = true;
      refreshExtras(); refreshNeed(); refreshWarnings(); refreshGroups();
    };
    const validate = () => {
      const messages = [];
      let first = null;
      for (const [name] of fields) {
        const message = checkField(name);
        if (message) { messages.push(message); if (!first) first = name; }
      }
      const { errors } = validateParams(entry, rawValues());
      for (const error of errors) {
        if (!error.group) continue;
        messages.push(error.message);
        for (const name of error.group) { const f = fields.get(name); if (f) f.control.focusEl.setAttribute("aria-invalid", "true"); }
        if (!first) first = error.group[0];
      }
      refreshNeed();
      problems.replaceChildren(...(messages.length ? [h("div", { class: "sk-note error" }, icon("warn"), h("div", {}, h("strong", { text: messages.length === 1 ? "Fix this before running" : `Fix these ${messages.length} things before running` }), h("ul", {}, [...new Set(messages)].map((m) => h("li", { text: m })))))] : []));
      if (first) {
        const field = fields.get(first);
        const g = groupNodes.get(firstGroupOf(field.param));
        if (g) g.details.open = true;
        field.node.hidden = false;
        field.control.focusEl.focus && field.control.focusEl.focus();
        field.node.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      return [...new Set(messages)];
    };
    const form = {
      node, entry,
      getValues, validate, setValues,
      reset: () => setValues({}),
      focusFirst: () => { const f = fields.values().next().value; if (f && f.control.focusEl.focus) f.control.focusEl.focus(); },
      getWarnings: () => validateParams(entry, rawValues()).warnings.map((w) => w.message),
    };
    setValues(initial);
    return form;
  }

  /** Request options that are not engine parameters. Returns {node, getValues(), setValues(obj)}. */
  function optionsForm(initial = {}, { onChange } = {}) {
    const fire = () => onChange && onChange(getValues());
    const noCache = toggleSwitch("Skip the cache", false, fire, { hint: "Always call the engine. A fresh search costs a credit." });
    const asyncSwitch = toggleSwitch("Async", false, fire, { hint: "Return at once and fetch the result from the archive." });
    const zeroTrace = toggleSwitch("Zero trace", false, fire, { hint: "Ask the engine not to keep the search (enterprise plans)." });
    const unknown = toggleSwitch("Allow unknown parameters", false, fire, { hint: "Send parameters this catalog does not list." });
    const output = segmented([["json", "JSON"], ["html", "Raw HTML"], ["md", "Markdown"]], "json", fire, { label: "Output format" });
    const outputId = uid("out");
    const restrictor = h("input", { type: "text", id: uid("jr"), class: "sk-mono", autocomplete: "off", spellcheck: "false", placeholder: "organic_results[].{title,link}" });
    restrictor.addEventListener("input", fire);
    const node = h("div", { class: "sk-options", role: "group", "aria-label": "Request options" },
      h("div", { class: "sk-field" }, h("span", { class: "sk-label", id: outputId, text: "Output" }), output.node, h("div", { class: "sk-hint", text: "HTML stores the page the engine saw and Markdown its text rendering; both open in the page tab." })),
      h("div", { class: "sk-field" }, h("label", { class: "sk-label", for: restrictor.id, text: "JSON restrictor" }), restrictor, h("div", { class: "sk-hint", text: "Trim the response to the fields you name, for example organic_results[].{title,link}." })),
      h("div", { class: "sk-switch-grid" }, noCache.node, asyncSwitch.node, zeroTrace.node, unknown.node));
    output.node.setAttribute("aria-labelledby", outputId);
    const getValues = () => {
      const values = {};
      if (noCache.get()) values.no_cache = true;
      if (output.get() === "html" || output.get() === "md") values.output = output.get();
      if (asyncSwitch.get()) values.async = true;
      if (zeroTrace.get()) values.zero_trace = true;
      if (unknown.get()) values.allow_unknown = true;
      if (restrictor.value.trim()) values.json_restrictor = restrictor.value.trim();
      return values;
    };
    const setValues = (values) => {
      const v = values || {};
      noCache.set(v.no_cache === true || v.no_cache === "true");
      asyncSwitch.set(v.async === true || v.async === "true");
      zeroTrace.set(v.zero_trace === true || v.zero_trace === "true");
      unknown.set(v.allow_unknown === true || v.allow_unknown === "true");
      output.set(v.output === "html" || v.output === "md" ? v.output : "json");
      restrictor.value = v.json_restrictor ? String(v.json_restrictor) : "";
    };
    setValues(initial);
    return { node, getValues, setValues };
  }

  // ---- JSON tree ---------------------------------------------------------------------------------------------------------

  const redact = (text) => String(text).replace(/(api_key=)[^&\s"'<>]+/gi, "$1•••");
  const STRING_CAP = 240;
  const CHUNK = 100;

  /**
   * Collapsible, searchable JSON viewer. Containers build their children only when opened, long arrays render in
   * chunks of 100 (the next chunk loads when you scroll to the end), and the tree follows the ARIA tree pattern:
   * arrows move, Right/Left open and close, Enter toggles, "c" copies the path, "v" copies the value.
   */
  function jsonTree(value, { maxDepth = 2, search = true, label = "Response" } = {}) {
    const wrap = h("div", { class: "sk-treewrap" });
    const tree = h("div", { class: "sk-tree", role: "tree", "aria-label": `${label} as JSON. Arrow keys move, Right and Left open and close, C copies the path, V copies the value.` });
    const status = h("span", { class: "sk-tree-status", role: "status", "aria-live": "polite" });
    let found = null;
    let depthCap = maxDepth;
    let budget = 0;

    const matchCountUnder = (pathStr, length) => {
      if (!found) return 0;
      let high = -1;
      const prefix = `${pathStr}[`;
      for (const hit of found.matches) {
        if (!hit.startsWith(prefix)) continue;
        const end = hit.indexOf("]", prefix.length);
        const index = Number(hit.slice(prefix.length, end));
        if (Number.isFinite(index) && index > high) high = index;
      }
      return Math.min(length, high + 1);
    };

    const scalarNode = (val) => {
      if (val === null) return h("span", { class: "sk-null", text: "null" });
      if (typeof val === "boolean") return h("span", { class: "sk-bool", text: String(val) });
      if (typeof val === "number") return h("span", { class: "sk-num", text: String(val) });
      const text = redact(val);
      const link = safeLink(text);
      const holder = h("span", { class: "sk-str" });
      let expanded = false;
      const paint = () => {
        const shown = expanded || text.length <= STRING_CAP ? text : `${text.slice(0, STRING_CAP)}…`;
        const body = link && text.length <= 2048 ? h("a", { href: link, target: "_blank", rel: "noopener noreferrer", text: `"${shown}"` }) : document.createTextNode(`"${shown}"`);
        holder.replaceChildren(body);
        if (text.length > STRING_CAP) holder.append(" ", h("button", { type: "button", class: "sk-more", tabindex: "-1", text: expanded ? "less" : `+${text.length - STRING_CAP} more`, onclick: (event) => { event.stopPropagation(); expanded = !expanded; paint(); } }));
      };
      paint();
      return holder;
    };

    const summaryOf = (val) => {
      if (Array.isArray(val)) return `[${val.length}]`;
      const keys = Object.keys(val);
      return `{${keys.slice(0, 4).join(", ")}${keys.length > 4 ? ", …" : ""}}`;
    };

    function renderNode(key, val, path, depth) {
      const pathStr = pathToString(path);
      const container = val !== null && typeof val === "object";
      const entries = container ? (Array.isArray(val) ? val.map((v, i) => [i, v]) : Object.entries(val)) : [];
      budget -= 1;
      let open = container && entries.length > 0 && budget > 0 && (depth < depthCap || (found && found.open.has(pathStr)));
      let built = false;
      let visible = 0;
      const hit = !!(found && found.matches.has(pathStr));
      const caret = container && entries.length ? h("button", { type: "button", class: "sk-caret", tabindex: "-1", "aria-hidden": "true" }, icon("chevron")) : h("span", { class: "sk-caret-sp" });
      const keyNode = key === null ? null : h("span", { class: typeof key === "number" ? "sk-idx" : "sk-k", text: typeof key === "number" ? `${key}` : key });
      const preview = container ? h("span", { class: "sk-sum", text: summaryOf(val) }) : null;
      const copyValue = () => redact(container ? JSON.stringify(val, null, 2).slice(0, 2_000_000) : String(val));
      const actions = h("span", { class: "sk-actions" },
        h("button", { type: "button", class: "sk-act", tabindex: "-1", title: "Copy path", "aria-label": `Copy path ${pathStr || "root"}`, text: "path", onclick: (event) => { event.stopPropagation(); copyText(pathStr || "(root)", "Path copied"); } }),
        h("button", { type: "button", class: "sk-act", tabindex: "-1", title: "Copy value", "aria-label": "Copy value", text: "value", onclick: (event) => { event.stopPropagation(); copyText(copyValue(), "Value copied"); } }));
      const row = h("div", { class: `sk-row${hit ? " hit" : ""}`, role: "treeitem", tabindex: "-1", "aria-level": String(depth + 1), "aria-expanded": container && entries.length ? String(open) : null, dataset: { path: pathStr } },
        caret, keyNode, keyNode ? h("span", { class: "sk-colon", text: ":" }) : null,
        container ? (entries.length ? preview : h("span", { class: "sk-sum", text: Array.isArray(val) ? "[]" : "{}" })) : scalarNode(val), actions);
      const kids = container && entries.length ? h("div", { class: "sk-kids", role: "group", hidden: !open }) : null;
      const node = h("div", { class: "sk-node" }, row, kids);

      const fill = () => {
        const total = entries.length;
        const need = Math.max(Math.min(total, CHUNK), matchCountUnder(pathStr, total));
        const target = Math.min(total, Math.max(visible + (visible ? CHUNK : 0), need));
        kids.querySelector(":scope > .sk-more-row")?.remove();
        for (let i = visible; i < target; i += 1) kids.append(renderNode(entries[i][0], entries[i][1], [...path, entries[i][0]], depth + 1));
        visible = target;
        if (visible < total) {
          const more = h("div", { class: "sk-more-row" }, h("button", { type: "button", class: "btn secondary sm", text: `Show ${Math.min(CHUNK, total - visible)} more of ${total - visible}`, onclick: () => fill() }));
          kids.append(more);
          if ("IntersectionObserver" in window) {
            const observer = new IntersectionObserver((list) => { if (list.some((e) => e.isIntersecting) && more.isConnected) { observer.disconnect(); fill(); } }, { rootMargin: "200px" });
            observer.observe(more);
          }
        }
      };
      const set = (next) => {
        if (!kids) return;
        open = next;
        if (open && !built) { built = true; fill(); }
        kids.hidden = !open;
        row.setAttribute("aria-expanded", String(open));
      };
      if (open) { built = true; fill(); }
      row.toggle = set;
      row.isOpen = () => open;
      row.addEventListener("click", (event) => { if (event.target.closest("a, .sk-act, .sk-more")) return; row.focus(); if (kids) set(!open); });
      row.copyPath = () => copyText(pathStr || "(root)", "Path copied");
      row.copyValue = () => copyText(copyValue(), "Value copied");
      return node;
    }

    const render = () => {
      budget = 6000;
      tree.replaceChildren(renderNode(null, value, [], 0));
      const first = tree.querySelector(".sk-row");
      if (first) first.tabIndex = 0;
    };

    const visibleRows = () => [...tree.querySelectorAll(".sk-row")].filter((row) => !row.closest("[hidden]"));
    tree.addEventListener("focusin", (event) => {
      const row = event.target.closest(".sk-row");
      if (!row) return;
      tree.querySelectorAll(".sk-row[tabindex='0']").forEach((r) => { r.tabIndex = -1; });
      row.tabIndex = 0;
    });
    tree.addEventListener("keydown", (event) => {
      const row = event.target.closest && event.target.closest(".sk-row");
      if (!row || event.target !== row) return;
      const rows = visibleRows();
      const index = rows.indexOf(row);
      const go = (target) => { if (target) { event.preventDefault(); target.focus(); target.scrollIntoView({ block: "nearest" }); } };
      switch (event.key) {
        case "ArrowDown": go(rows[index + 1]); break;
        case "ArrowUp": go(rows[index - 1]); break;
        case "Home": go(rows[0]); break;
        case "End": go(rows[rows.length - 1]); break;
        case "ArrowRight":
          if (row.hasAttribute("aria-expanded")) { event.preventDefault(); if (!row.isOpen()) row.toggle(true); else go(rows[index + 1]); }
          break;
        case "ArrowLeft":
          if (row.hasAttribute("aria-expanded") && row.isOpen()) { event.preventDefault(); row.toggle(false); }
          else { const parent = row.parentElement.parentElement.closest(".sk-node"); go(parent && parent.querySelector(":scope > .sk-row")); }
          break;
        case "Enter":
        case " ":
          if (row.hasAttribute("aria-expanded")) { event.preventDefault(); row.toggle(!row.isOpen()); }
          break;
        case "c": if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); row.copyPath(); } break;
        case "v": if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); row.copyValue(); } break;
        default: break;
      }
    });

    if (search) {
      const input = h("input", { type: "search", class: "sk-tree-search", placeholder: "Search keys and values", "aria-label": "Search the JSON", autocomplete: "off" });
      const run = debounce(() => {
        found = input.value.trim() ? searchTree(value, input.value, { limit: 500 }) : null;
        status.textContent = found ? (found.count ? `${found.count}${found.truncated ? "+" : ""} match${found.count === 1 ? "" : "es"}` : "No matches") : "";
        render();
        if (found && found.count) { const first = tree.querySelector(".sk-row.hit"); if (first) first.scrollIntoView({ block: "center" }); }
      }, 200);
      input.addEventListener("input", run);
      input.addEventListener("keydown", (event) => { if (event.key === "Escape" && input.value) { event.stopPropagation(); input.value = ""; run(); } });
      wrap.append(h("div", { class: "sk-tree-bar" }, h("div", { class: "sk-searchbox" }, icon("search"), input), status,
        h("div", { class: "btn-row" },
          h("button", { type: "button", class: "btn secondary sm", text: "Expand", onclick: () => { depthCap = 6; render(); } }),
          h("button", { type: "button", class: "btn secondary sm", text: "Collapse", onclick: () => { depthCap = 1; found = null; input.value = ""; status.textContent = ""; render(); } }),
          copyButton(() => redact(JSON.stringify(value, null, 2).slice(0, 5_000_000)), { label: "Copy JSON", text: true }))));
    }
    wrap.append(tree);
    if (value === undefined || value === null) tree.append(note("There is no response body to show.", ""));
    else render();
    return wrap;
  }

  // ---- code snippets -------------------------------------------------------------------------------------------------------

  function codeBlock(lang, code, { label } = {}) {
    const body = h("code", { class: `sk-lang-${lang}` });
    for (const token of tokenize(lang, code)) body.append(token.type === "plain" ? document.createTextNode(token.text) : h("span", { class: `t-${token.type}`, text: token.text }));
    return h("div", { class: "sk-code" },
      h("div", { class: "sk-code-head" }, h("span", { class: "sk-code-lang", text: label || LANGUAGE_LABELS[lang] || lang }), copyButton(code, { label: "Copy code", message: "Snippet copied", text: true })),
      h("pre", { tabindex: "0", "aria-label": `${label || LANGUAGE_LABELS[lang] || lang} code` }, body));
  }

  function engineId(engine) { return typeof engine === "string" ? engine : (engine && (engine.id || engine.engine)) || "google"; }

  /** {curl, python, javascript, ruby, php, go, java, csharp}: plain strings against the local /v1 endpoint by default. */
  function codeSnippets(engine, params, { baseUrl, apiKey } = {}) {
    return buildSnippets(engineId(engine), params || {}, { baseUrl, apiKey });
  }

  /**
   * Language tabs with copy buttons. Returns a node with update(engine, params) so a form can keep it live.
   * Target switch: the Aira endpoint (default) or SerpApi directly.
   */
  function snippetPanel(engine, params, { baseUrl } = {}) {
    let currentEngine = engine;
    let currentParams = params || {};
    let lang = Aira.recall("serp.lang", "curl");
    if (!LANGUAGES.includes(lang)) lang = "curl";
    let target = baseUrl ? "custom" : Aira.recall("serp.target", "aira");
    const origin = root.location && root.location.origin ? `${root.location.origin}/v1` : "http://127.0.0.1:8765/v1";
    const tabs = h("div", { class: "sk-langs", role: "tablist", "aria-label": "Language", dataset: { roving: "select" } });
    const panel = h("div", { class: "sk-snippet", role: "tabpanel" });
    const targetSeg = segmented([["aira", "API"], ["serpapi", "SerpApi direct"]], target === "serpapi" ? "serpapi" : "aira", (next) => { target = next; Aira.remember("serp.target", next); paint(); }, { label: "Endpoint" });
    const hint = h("p", { class: "sk-hint" });
    const paint = () => {
      const direct = target === "serpapi";
      const code = buildSnippets(engineId(currentEngine), currentParams, { baseUrl: direct ? "https://serpapi.com" : (baseUrl || origin), apiKey: direct ? "YOUR_SERPAPI_API_KEY" : "YOUR_AIRA_API_KEY" });
      for (const button of tabs.children) { const on = button.dataset.lang === lang; button.setAttribute("aria-selected", String(on)); button.tabIndex = on ? 0 : -1; }
      panel.replaceChildren(codeBlock(lang, code[lang]));
      hint.textContent = direct ? "Calls SerpApi directly and uses your own SerpApi key. Monosage is not involved, so nothing is cached or recorded." : "Create an API key on the Keys tab. Calls through Monosage are cached, recorded in History and counted in Usage.";
    };
    for (const code of LANGUAGES) {
      tabs.append(h("button", { type: "button", role: "tab", class: "sk-lang-tab", dataset: { lang: code }, text: LANGUAGE_LABELS[code], onclick: () => { lang = code; Aira.remember("serp.lang", code); paint(); } }));
    }
    rovingKeys(tabs, "button");
    const node = h("div", { class: "sk-snippets" }, h("div", { class: "sk-snippet-bar" }, tabs, baseUrl ? null : targetSeg.node), panel, hint);
    node.update = (nextEngine, nextParams) => { currentEngine = nextEngine; currentParams = nextParams || {}; paint(); };
    paint();
    return node;
  }

  // ---- badges and chips ---------------------------------------------------------------------------------------------------

  function hashHue(text) {
    let hash = 0;
    for (const ch of String(text)) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
    return hash;
  }
  /** Pill with a coloured tile for the engine family. Accepts a catalog entry, a summary row or an engine id. */
  function engineBadge(engine) {
    const id = engineId(engine);
    const info = typeof engine === "string" ? {} : engine || {};
    const name = info.name || info.engine_name || humanize(id);
    const family = info.family || info.category || id.split("_")[0];
    return h("span", { class: "sk-engine", title: id, style: { "--hue": String(hashHue(family)) } },
      h("span", { class: "sk-engine-tile", "aria-hidden": "true", text: name.trim().charAt(0).toUpperCase() }), h("span", { class: "sk-engine-name", text: name }));
  }

  function creditChip(record) {
    const r = record || {};
    if (r.cache_hit) return h("span", { class: "sk-credit cache", title: "Served from the local cache. No search was used." }, icon("check"), h("span", { text: "Cached · 0 credits" }));
    if (r.status === "error") return h("span", { class: "sk-credit none", title: "Failed searches are not counted." }, h("span", { text: "No credit used" }));
    if (r.status === "running") return h("span", { class: "sk-credit none" }, h("span", { text: "Pending" }));
    const n = r.credits === undefined || r.credits === null ? 1 : r.credits;
    return h("span", { class: "sk-credit", title: "Searches counted against the plan" }, h("span", { text: `${n} credit${n === 1 ? "" : "s"}` }));
  }

  function statusBadge(record) {
    const r = record || {};
    if (r.status === "error") return h("span", { class: "badge sk-status err", title: r.error || "The search failed" }, h("span", { "aria-hidden": "true", text: "!" }), h("span", { text: r.http_status ? `Error ${r.http_status}` : "Error" }));
    if (r.status === "running") return h("span", { class: "badge sk-status run" }, h("span", { class: "sk-pulse", "aria-hidden": "true" }), h("span", { text: "Running" }));
    return h("span", { class: "badge sk-status ok", title: r.http_status ? `HTTP ${r.http_status}` : "" }, h("span", { "aria-hidden": "true", text: "✓" }), h("span", { text: "Success" }));
  }

  /** Resolves true when the person accepts spending up to n searches. */
  /**
   * Ask before an action that spends searches. ``live``: the searches always run live (monitor and rank
   * checks), so the cache does not help. A second click of a double click lands on the backdrop as soon as the
   * question opens; it is ignored for a moment so the question stays open instead of quietly cancelling.
   */
  function confirmCost(n, label = "This action", { live = false } = {}) {
    const count = Math.max(0, Math.round(Number(n) || 0));
    const plural = `search${count === 1 ? "" : "es"}`;
    const note = live ? `Each check runs live, so ${count === 1 ? "it uses a search" : "each one uses a search"}.` : "Results that are already cached cost nothing.";
    const title = `Use ${live ? "" : "up to "}${count} ${plural}?`;
    const message = `${label} runs ${count} ${plural} against your plan. ${note}`;
    const confirmLabel = `Run${count ? ` (${live ? "" : "up to "}${count})` : ""}`;
    if (!hasDom || typeof Aira.modal !== "function") return Aira.confirm({ title, message, confirmLabel });
    return new Promise((resolve) => {
      let answered = false;
      const answer = (value) => { if (!answered) { answered = true; resolve(value); } };
      const opened = Date.now();
      const dialog = Aira.modal({
        title,
        body: h("p", { class: "muted", text: message }),
        actions: [
          { label: "Cancel", kind: "ghost", onClick: () => answer(false) },
          { label: confirmLabel, kind: "", onClick: () => answer(true) },
        ],
        onClose: () => answer(false),
      });
      const backdrop = dialog && dialog.box ? dialog.box.parentElement : null;
      if (backdrop) backdrop.addEventListener("click", (event) => { if (event.target === backdrop && Date.now() - opened < 700) event.stopImmediatePropagation(); }, true);
    });
  }

  // ===========================================================================
  // Visual renderers
  // ===========================================================================

  const SECTION_TITLES = {
    organic_results: "Organic results", ai_overview: "AI overview", text_blocks: "AI answer", markdown: "Answer", reconstructed_markdown: "Answer",
    answer_box: "Answer box", knowledge_graph: "Knowledge graph", related_questions: "People also ask", related_searches: "Related searches",
    top_stories: "Top stories", news_results: "News", jobs_results: "Jobs", shopping_results: "Shopping", inline_shopping_results: "Shopping",
    immersive_products: "Products", related_products: "Related products", local_results: "Local results", places: "Places", place_results: "Place",
    images_results: "Images", inline_images: "Images", image_results: "Images", visual_matches: "Visual matches", photos: "Photos",
    video_results: "Videos", videos_results: "Videos", inline_videos: "Videos", short_video_results: "Short videos", shorts_results: "Shorts", movies_results: "Movies",
    best_flights: "Best flights", other_flights: "Other flights", price_insights: "Price insights", properties: "Properties",
    summary: "Quote", market_trends: "Markets", interest_over_time: "Interest over time", interest_by_region: "Interest by region",
    compared_breakdown_by_region: "Compared by region", related_queries: "Related queries", related_topics: "Related topics", trending_searches: "Trending now",
    reviews: "Reviews", reviews_results: "Reviews", user_reviews: "Reviews", ratings: "Rating breakdown", articles: "Articles", profiles: "Profiles",
    cited_by: "Citations", events_results: "Events", ads: "Ads", ads_results: "Ads", sponsored_brands: "Sponsored brands", suggestions: "Suggestions",
    suggested_searches: "Suggested searches", top_searches: "Top searches", categories: "Categories", topics: "Topics", filters: "Filters",
    transcript: "Transcript", chapters: "Chapters", product_results: "Product", product_result: "Product", refine_this_search: "Refine this search",
  };
  const titleForKey = (key) => SECTION_TITLES[key] || humanize(key);

  const sameList = (value, ...inner) => {
    if (Array.isArray(value)) return value;
    if (isObject(value)) {
      for (const key of inner) if (Array.isArray(value[key])) return value[key];
      const firstList = Object.values(value).find((v) => Array.isArray(v) && v.some(isObject));
      if (firstList) return firstList;
    }
    return [];
  };

  /** Progressive list: renders `pageSize` items and a "Show more" button for the rest. */
  function paged(list, renderItem, { pageSize = 24, cls = "sk-list", tag = "div" } = {}) {
    const container = h(tag, { class: cls });
    const more = h("div", { class: "sk-more-row" });
    let shown = 0;
    const next = () => {
      const stop = Math.min(list.length, shown + pageSize);
      for (; shown < stop; shown += 1) { const node = renderItem(list[shown], shown); if (node) container.append(node); }
      more.replaceChildren();
      if (shown < list.length) more.append(h("button", { type: "button", class: "btn secondary sm", text: `Show ${Math.min(pageSize, list.length - shown)} more (${list.length - shown} left)`, onclick: next }));
    };
    next();
    return h("div", { class: "sk-paged" }, container, more);
  }

  const posBadge = (n) => (n === undefined || n === null || n === "" ? null : h("span", { class: "sk-pos", "aria-label": `Position ${n}`, text: String(n) }));
  const titleText = (item, fallback = "Untitled") => titleOfItem(item) || fallback;
  const moneyText = (value, currency) => {
    if (typeof value !== "number") return textOf(value);
    try { return new Intl.NumberFormat([], { style: "currency", currency: currency || "USD", maximumFractionDigits: value % 1 ? 2 : 0 }).format(value); }
    catch { return `${currency || "$"}${value}`; }
  };
  const sourceName = (source) => (isObject(source) ? source.name || source.title || "" : textOf(source));
  const dateChip = (label, value) => (value ? h("span", { class: "sk-kvchip" }, h("span", { text: label }), h("b", { text: textOf(value) })) : null);

  /** Rows of key/value pairs for unknown scalar fields. */
  function kvList(obj, { max = 40, skip = [], rawKeys = false } = {}) {
    const flat = flattenObject(obj || {}, { maxDepth: 3 });
    const pairs = Object.entries(flat).filter(([k, v]) => !skip.includes(k) && !isEmpty(v) && v !== "[0 items]").slice(0, max);
    if (!pairs.length) return null;
    return h("dl", { class: "sk-kv" }, pairs.flatMap(([k, v]) => {
      const text = redact(String(v));
      const link = safeLink(text);
      return [h("dt", { text: rawKeys ? k : humanize(k) }), h("dd", {}, link && !/api_key=/i.test(text) ? extLink(link, text) : text)];
    }));
  }

  // ---- charts ---------------------------------------------------------------------------------------------------------------

  /**
   * Responsive SVG line/area chart with a snapping crosshair, one tooltip for every series, keyboard stepping, a legend
   * for two or more series and a data table behind a disclosure. `compact` draws a bare sparkline.
   */
  function lineChart({ series, labels, height = 220, yMin, yMax, format = (v) => fmtNumber(Math.round(v * 100) / 100), area = false, compact = false, ariaLabel = "Line chart", tableLabel = "Date" }) {
    const wrap = h("figure", { class: `sk-chart${compact ? " compact" : ""}` });
    const frame = h("div", { class: "sk-chart-frame" });
    const tip = h("div", { class: "sk-tip", hidden: true, role: "presentation" });
    const count = Math.max(...series.map((s) => s.values.length), 0);
    const hasData = series.some((s) => s.values.some((v) => typeof v === "number"));
    let width = 0;
    let active = -1;
    let draw = () => {};

    const labelAt = (i) => (labels && labels[i] !== undefined ? String(labels[i]) : String(i + 1));
    const showAt = (i, geometry, svgNode, overlayX) => {
      active = clamp(i, 0, count - 1);
      const x = geometry.xOf(active);
      svgNode.querySelector(".sk-cross").setAttribute("x1", x);
      svgNode.querySelector(".sk-cross").setAttribute("x2", x);
      svgNode.querySelector(".sk-cross").style.display = "";
      svgNode.querySelectorAll(".sk-dot-mark").forEach((dot, k) => {
        const v = series[k].values[active];
        if (typeof v !== "number") { dot.style.display = "none"; return; }
        dot.style.display = "";
        dot.setAttribute("cx", x);
        dot.setAttribute("cy", geometry.yOf(v));
      });
      tip.replaceChildren(h("div", { class: "sk-tip-label", text: labelAt(active) }),
        ...series.map((s, k) => (typeof s.values[active] === "number" ? h("div", { class: "sk-tip-row" }, h("i", { class: `sk-key s${(k % 5) + 1}`, style: s.color ? { background: s.color } : null }), h("b", { text: format(s.values[active]) }), series.length > 1 ? h("span", { text: s.name }) : null) : null)));
      tip.hidden = false;
      const left = clamp(x, 8, width - 8);
      tip.style.left = `${left}px`;
      tip.classList.toggle("flip", left > width * 0.62);
    };
    const hideTip = (svgNode) => {
      tip.hidden = true;
      active = -1;
      svgNode.querySelector(".sk-cross").style.display = "none";
      svgNode.querySelectorAll(".sk-dot-mark").forEach((d) => { d.style.display = "none"; });
    };

    draw = () => {
      width = Math.max(240, Math.floor(frame.clientWidth || 640));
      const padLeft = compact ? 2 : 44;
      const padRight = compact ? 2 : 12;
      const padTop = compact ? 4 : 10;
      const padBottom = compact ? 4 : 24;
      const h2 = compact ? Math.min(height, 56) : height;
      const all = series.flatMap((s) => s.values.filter((v) => typeof v === "number"));
      let lo = yMin !== undefined ? yMin : all.length ? Math.min(...all) : 0;
      let hi = yMax !== undefined ? yMax : all.length ? Math.max(...all) : 1;
      const room = (hi - lo || Math.abs(hi) || 1) * 0.06;
      if (yMin === undefined) lo -= room;
      if (yMax === undefined) hi += room;
      if (lo === hi) hi = lo + 1;
      const geoOpts = { width, height: h2, padLeft, padRight, padTop, padBottom, count, min: lo, max: hi };
      const geometries = series.map((s) => seriesGeometry(s.values, geoOpts));
      const base = geometries[0] || seriesGeometry([], geoOpts);
      const ticks = compact ? [] : niceTicks(base.min, base.max, 4);
      const node = svg("svg", { class: "sk-svg", viewBox: `0 0 ${width} ${h2}`, width, height: h2, role: "img", "aria-label": ariaLabel, tabindex: compact ? null : "0", focusable: compact ? "false" : "true" });
      for (const t of ticks) {
        const y = base.yOf(t);
        node.append(svg("line", { class: "sk-gridline", x1: padLeft, x2: width - padRight, y1: y, y2: y }), svg("text", { class: "sk-axis", x: padLeft - 6, y: y + 3.5, "text-anchor": "end", text: format(t) }));
      }
      if (!compact && count > 0) {
        const picks = count <= 2 ? [0, count - 1] : [0, Math.round((count - 1) / 2), count - 1];
        [...new Set(picks)].forEach((i, k, arr) => node.append(svg("text", { class: "sk-axis", x: base.xOf(i), y: h2 - 6, "text-anchor": k === 0 ? "start" : k === arr.length - 1 ? "end" : "middle", text: labelAt(i).slice(0, 22) })));
      }
      if (area && geometries[0]) node.append(svg("path", { class: "sk-area s1", d: geometries[0].area }));
      geometries.forEach((g, k) => node.append(svg("path", { class: `sk-line s${(k % 5) + 1}`, d: g.line, style: series[k].color ? `stroke:${series[k].color}` : null })));
      if (!compact) {
        node.append(svg("line", { class: "sk-cross", x1: 0, x2: 0, y1: padTop, y2: h2 - padBottom, style: "display:none" }));
        series.forEach((s, k) => node.append(svg("circle", { class: `sk-dot-mark s${(k % 5) + 1}`, r: 4, cx: 0, cy: 0, style: "display:none" })));
        const pointerIndex = (event) => {
          const rect = node.getBoundingClientRect();
          const x = ((event.clientX - rect.left) / rect.width) * width;
          return clamp(Math.round(((x - padLeft) / Math.max(1, width - padLeft - padRight)) * (count - 1)), 0, count - 1);
        };
        node.addEventListener("pointermove", (event) => showAt(pointerIndex(event), base, node));
        node.addEventListener("pointerleave", () => hideTip(node));
        node.addEventListener("focus", () => showAt(active < 0 ? count - 1 : active, base, node));
        node.addEventListener("blur", () => hideTip(node));
        node.addEventListener("keydown", (event) => {
          if (event.key === "ArrowRight") { event.preventDefault(); showAt((active < 0 ? -1 : active) + 1, base, node); }
          else if (event.key === "ArrowLeft") { event.preventDefault(); showAt((active < 0 ? count : active) - 1, base, node); }
          else if (event.key === "Home") { event.preventDefault(); showAt(0, base, node); }
          else if (event.key === "End") { event.preventDefault(); showAt(count - 1, base, node); }
          else if (event.key === "Escape") hideTip(node);
        });
      }
      frame.replaceChildren(node, tip);
    };

    if (!hasData) { wrap.append(note("This series has no numeric values to chart.", "")); return wrap; }
    wrap.append(frame);
    if (!compact && series.length > 1) {
      wrap.append(h("ul", { class: "sk-legend" }, series.map((s, k) => h("li", {}, h("i", { class: `sk-key s${(k % 5) + 1}` }), h("span", { text: s.name })))));
    }
    if (!compact) {
      const rows = Math.min(count, 400);
      const table = h("div", { class: "table-wrap sk-chart-table" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", { text: tableLabel }), series.map((s) => h("th", { class: "num", text: s.name })))),
        h("tbody", {}, Array.from({ length: rows }, (_, i) => h("tr", {}, h("td", { text: labelAt(i) }), series.map((s) => h("td", { class: "num", text: typeof s.values[i] === "number" ? format(s.values[i]) : "" })))))));
      wrap.append(h("details", { class: "sk-chart-data" }, h("summary", { text: "View data table" }), table));
    }
    if (typeof ResizeObserver !== "undefined") {
      let last = 0;
      const observer = new ResizeObserver(() => { const w = Math.floor(frame.clientWidth); if (w && w !== last) { last = w; draw(); } });
      observer.observe(frame);
    } else requestAnimationFrame(draw);
    queueMicrotask(() => draw());
    return wrap;
  }

  /** Horizontal bars with the value as text at the end of each bar. rows: [{label, value, sub, series}]. */
  function barRows(rows, { max, format = (v) => fmtNumber(v) } = {}) {
    const top = max !== undefined ? max : Math.max(...rows.map((r) => Math.max(...(r.values || [r.value]).map((v) => v || 0))), 1);
    return h("div", { class: "sk-bars", role: "list" }, rows.map((row) => h("div", { class: "sk-bar-row", role: "listitem" },
      h("span", { class: "sk-bar-label", title: row.label, text: row.label }),
      h("span", { class: "sk-bar-tracks" }, (row.values || [row.value]).map((v, k) => h("span", { class: "sk-bar-track" }, h("i", { class: `s${(k % 5) + 1}`, style: { width: `${clamp(((v || 0) / top) * 100, 0, 100)}%` } })))),
      h("span", { class: "sk-bar-num", text: (row.values || [row.value]).map((v) => (v === null || v === undefined ? "–" : format(v))).join(" / ") }))));
  }

  // ---- general result items -------------------------------------------------------------------------------------------

  function webItem(item) {
    const link = item.link || item.url;
    const site = item.displayed_link || sourceName(item.source) || hostOf(link);
    const rich = (item.rich_snippet && (item.rich_snippet.top || item.rich_snippet.bottom)) || {};
    const extensions = [...(rich.extensions || []), ...(item.extensions || [])].slice(0, 5);
    const sitelinks = (item.sitelinks && (item.sitelinks.inline || item.sitelinks.expanded)) || (Array.isArray(item.sitelinks) ? item.sitelinks : []);
    return h("article", { class: "sk-item" },
      h("div", { class: "sk-item-site" }, posBadge(item.position), item.favicon ? thumb(item.favicon, { cls: "fav", alt: "" }) : null, h("span", { text: site })),
      extLink(link, titleText(item, link || "Untitled"), { class: "sk-item-title" }),
      item.snippet ? h("p", { class: "sk-text" }, highlighted(item.snippet, item.snippet_highlighted_words)) : null,
      metaLine(item.date, rich.detected_extensions && rich.detected_extensions.rating ? stars(rich.detected_extensions.rating, { count: rich.detected_extensions.reviews }) : null, ...extensions.map(textOf)),
      sitelinks.length ? h("div", { class: "sk-sitelinks" }, sitelinks.slice(0, 6).map((s) => extLink(s.link, s.title || s.link))) : null);
  }

  function scholarItem(item) {
    const info = item.publication_info || {};
    const cited = (item.inline_links && item.inline_links.cited_by) || (isObject(item.cited_by) ? item.cited_by : null);
    const authors = typeof item.authors === "string" ? item.authors : Array.isArray(info.authors) ? info.authors.map((a) => a.name).join(", ") : "";
    const resources = Array.isArray(item.resources) ? item.resources : [];
    return h("article", { class: "sk-item scholar" },
      h("div", { class: "sk-item-site" }, posBadge(item.position), item.type ? chip(item.type, "tone") : null, h("span", { text: hostOf(item.link) })),
      extLink(item.link, titleText(item, item.link || "Untitled"), { class: "sk-item-title" }),
      h("div", { class: "sk-byline", text: info.summary || [authors, item.publication, item.year].filter(Boolean).join(" · ") }),
      item.snippet ? h("p", { class: "sk-text", text: item.snippet }) : null,
      chipRow([
        cited && (cited.total !== undefined || cited.value !== undefined) ? extLink(cited.link, `Cited by ${fmtNumber(cited.total !== undefined ? cited.total : cited.value)}`, { class: "sk-chip link" }) : null,
        item.inline_links && item.inline_links.versions ? chip(`${item.inline_links.versions.total || ""} versions`.trim()) : null,
        ...resources.map((r) => extLink(r.link, `${r.title || "Resource"}${r.file_format ? ` [${r.file_format}]` : ""}`, { class: "sk-chip link" })),
      ]));
  }

  function patentItem(item) {
    const id = item.publication_number || item.patent_id || item.id;
    return h("article", { class: "sk-item patent" },
      h("div", { class: "sk-item-site" }, posBadge(item.position || item.rank), id ? chip(id, "tone") : null, item.country_status ? chip(textOf(item.country_status)) : null, item.language ? chip(item.language) : null),
      extLink(item.patent_link || item.link || item.pdf, titleText(item, id || "Patent"), { class: "sk-item-title" }),
      h("div", { class: "sk-byline", text: [item.assignee, item.inventor].filter(Boolean).join(" · ") }),
      item.snippet ? h("p", { class: "sk-text", text: item.snippet }) : null,
      h("div", { class: "sk-chips" }, [dateChip("Priority", item.priority_date), dateChip("Filed", item.filing_date), dateChip("Granted", item.grant_date), dateChip("Published", item.publication_date)].filter(Boolean),
        item.pdf ? extLink(item.pdf, "PDF", { class: "sk-chip link" }) : null));
  }

  function appItem(item) {
    const rating = stars(item.rating, { count: item.reviews || item.rating_count });
    const price = item.price && isObject(item.price) ? item.price.price || item.price.type : item.price || item.extracted_price;
    const logo = imageField(item, "thumbnail", "icon") || (Array.isArray(item.logos) && item.logos.length ? (item.logos[item.logos.length - 1] || {}).link : "");
    return h("article", { class: "sk-app" },
      thumb(logo, { cls: "appicon", alt: "", fallback: item.title }),
      h("div", { class: "sk-app-body" },
        extLink(item.link || item.product_link, titleText(item), { class: "sk-item-title" }),
        h("div", { class: "sk-byline", text: textOf(item.author || item.developer || item.seller || item.publisher) }),
        rating,
        metaLine(textOf(item.category || item.genres), item.downloads ? `${item.downloads} downloads` : "", price !== undefined && price !== null && price !== "" ? textOf(price) : ""),
        item.description || item.snippet ? h("p", { class: "sk-text clamp", text: textOf(item.description || item.snippet) }) : null));
  }

  function appSection(item) {
    return h("div", { class: "sk-subsection" }, h("h4", { text: item.title || "Apps" }),
      h("div", { class: "sk-grid-cards" }, item.items.filter(isObject).map(appItem)));
  }

  function organicItem(item) {
    if (!isObject(item)) return null;
    if (Array.isArray(item.items) && item.items.some(isObject)) return appSection(item);
    if (item.publication_info || (item.inline_links && item.inline_links.cited_by) || Array.isArray(item.resources) || (typeof item.authors === "string" && item.publication)) return scholarItem(item);
    if (item.publication_number || item.patent_id || item.priority_date || item.inventor || item.assignee) return patentItem(item);
    if (item.bundle_id || item.product_id || item.genres || item.downloads) return appItem(item);
    return webItem(item);
  }

  /** Last-resort card for any titled record. */
  function genericCard(item) {
    if (!isObject(item)) return h("div", { class: "sk-item" }, h("p", { class: "sk-text", text: textOf(item) }));
    const flat = flattenObject(item, { maxDepth: 2 });
    const used = new Set(["title", "name", "link", "snippet", "description", "thumbnail", "image", "favicon", "position", "query", "text", "question"]);
    const extra = Object.entries(flat).filter(([k, v]) => !used.has(k.split(".")[0]) && typeof v !== "object" && !isEmpty(v) && String(v).length <= 60).slice(0, 5);
    return h("article", { class: "sk-item generic" },
      h("div", { class: "sk-item-row" }, imageField(item) ? thumb(imageField(item), { cls: "side", alt: "" }) : null,
        h("div", { class: "sk-item-main" },
          h("div", { class: "sk-item-site" }, posBadge(item.position), item.source ? h("span", { text: sourceName(item.source) }) : item.displayed_link ? h("span", { text: item.displayed_link }) : null),
          extLink(item.link || item.url, titleText(item, "Item"), { class: "sk-item-title" }),
          item.snippet || item.description ? h("p", { class: "sk-text clamp", text: textOf(item.snippet || item.description) }) : null,
          extra.length ? metaLine(extra.map(([k, v]) => `${humanize(k)}: ${v}`)) : null)));
  }

  // ---- news, jobs, shopping, places, media -----------------------------------------------------------------------------

  function newsItem(item) {
    const source = item.source;
    const stories = Array.isArray(item.stories) ? item.stories : [];
    return h("article", { class: "sk-item news" },
      h("div", { class: "sk-item-row" },
        imageField(item) ? thumb(imageField(item), { cls: "side", alt: "" }) : null,
        h("div", { class: "sk-item-main" },
          h("div", { class: "sk-item-site" }, posBadge(item.position), isObject(source) && source.icon ? thumb(source.icon, { cls: "fav", alt: "" }) : null, h("span", { text: sourceName(source) || hostOf(item.link) }), item.date ? h("span", { class: "sk-dim", text: item.date }) : null),
          extLink(item.link, titleText(item, item.link || "Story"), { class: "sk-item-title" }),
          item.snippet ? h("p", { class: "sk-text clamp", text: item.snippet }) : null,
          isObject(source) && Array.isArray(source.authors) && source.authors.length ? h("div", { class: "sk-byline", text: `By ${source.authors.join(", ")}` }) : null)),
      stories.length ? h("div", { class: "sk-substories" }, stories.slice(0, 4).map((s) => h("div", {}, extLink(s.link, titleText(s), { class: "sk-sublink" }), h("span", { class: "sk-dim", text: [sourceName(s.source), s.date].filter(Boolean).join(" · ") })))) : null);
  }

  function jobItem(job) {
    const detected = job.detected_extensions || {};
    const highlights = Array.isArray(job.job_highlights) ? job.job_highlights : [];
    const applies = Array.isArray(job.apply_options) ? job.apply_options : [];
    const chips = [detected.posted_at, detected.schedule_type, detected.salary, detected.work_from_home ? "Remote" : "", detected.health_insurance ? "Health insurance" : "", detected.paid_time_off ? "Paid time off" : ""].filter(Boolean);
    const extras = (job.extensions || []).filter((e) => !chips.includes(e));
    return h("article", { class: "sk-item job" },
      h("div", { class: "sk-item-row" }, thumb(job.thumbnail, { cls: "side logo", alt: "", fallback: job.company_name }),
        h("div", { class: "sk-item-main" },
          h("div", { class: "sk-item-title plain", text: titleText(job, "Job") }),
          h("div", { class: "sk-byline", text: [job.company_name, job.location].filter(Boolean).join(" · ") + (job.via ? `  ${job.via.startsWith("via") ? job.via : `via ${job.via}`}` : "") }),
          chipRow([...chips.map((c) => chip(c, c === detected.salary ? "good" : c === "Remote" ? "tone" : "")), ...extras.slice(0, 3).map((e) => chip(e))]))),
      highlights.length || job.description ? h("details", { class: "sk-job-more" }, h("summary", { text: "Description and highlights" }),
        highlights.map((hl) => h("div", { class: "sk-highlight" }, h("strong", { text: hl.title }), h("ul", {}, (hl.items || []).map((i) => h("li", { text: i }))))),
        job.description ? h("p", { class: "sk-text pre", text: job.description }) : null) : null,
      applies.length || job.share_link ? h("div", { class: "btn-row" }, applies.slice(0, 4).map((a, i) => extLink(a.link, `Apply on ${a.title || hostOf(a.link) || "site"}`, { class: `btn${i ? " secondary" : ""} sm` })),
        job.share_link ? extLink(job.share_link, "Share link", { class: "btn ghost sm" }) : null) : null);
  }

  function shoppingItem(item) {
    const price = item.price !== undefined ? item.price : item.extracted_price;
    return h("article", { class: "sk-product" },
      thumb(imageField(item), { cls: "tile", alt: titleText(item, ""), fallback: titleText(item, "") }),
      h("div", { class: "sk-product-body" },
        h("div", { class: "sk-price" }, h("b", { text: textOf(price) }), item.old_price ? h("s", { text: textOf(item.old_price) }) : null, item.tag ? chip(item.tag, "good") : null),
        extLink(item.product_link || item.link, titleText(item, "Product"), { class: "sk-item-title" }),
        h("div", { class: "sk-byline", text: sourceName(item.source) || textOf(item.merchant) }),
        stars(item.rating, { count: item.reviews }), item.delivery ? h("div", { class: "sk-dim", text: textOf(item.delivery) }) : null));
  }

  const osmLink = (gps) => (gps && Number.isFinite(Number(gps.latitude)) && Number.isFinite(Number(gps.longitude)) ? `https://www.openstreetmap.org/?mlat=${gps.latitude}&mlon=${gps.longitude}#map=16/${gps.latitude}/${gps.longitude}` : null);

  function placeItem(place) {
    const gps = place.gps_coordinates;
    const map = osmLink(gps);
    const open = place.open_state || (isObject(place.hours) ? "" : place.hours);
    const options = isObject(place.service_options) ? Object.entries(place.service_options).filter(([, v]) => v).map(([k]) => humanize(k)) : Array.isArray(place.service_options) ? place.service_options.map(textOf) : [];
    return h("article", { class: "sk-item place" },
      h("div", { class: "sk-item-row" }, imageField(place) ? thumb(imageField(place), { cls: "side", alt: "" }) : null,
        h("div", { class: "sk-item-main" },
          h("div", { class: "sk-item-site" }, posBadge(place.position)),
          extLink(place.website || place.link, titleText(place, "Place"), { class: "sk-item-title" }),
          stars(place.rating, { count: place.reviews || place.user_ratings_total }),
          metaLine(textOf(place.type), place.price ? textOf(place.price) : "", place.address),
          metaLine(open ? textOf(open) : "", place.phone),
          chipRow(options.slice(0, 5)),
          h("div", { class: "sk-links" }, map ? extLink(map, [icon("map"), "Map"], { class: "sk-linkbtn" }) : null))));
  }

  function imageTile(item) {
    const src = imageField(item, "thumbnail", "image", "original");
    const target = item.link || item.original || item.image;
    const caption = titleOfItem(item) || sourceName(item.source) || "";
    return h("figure", { class: "sk-image" },
      extLink(target, [thumb(src, { cls: "frame", alt: caption, fallback: caption }), h("figcaption", {}, h("span", { text: caption }), sourceName(item.source) && caption ? h("small", { text: sourceName(item.source) }) : null)], { class: "sk-image-link", title: caption || null }));
  }

  function videoItem(video) {
    if (Array.isArray(video.shorts)) return h("div", { class: "sk-subsection" }, h("h4", { text: "Shorts" }), h("div", { class: "sk-grid-cards" }, video.shorts.map(videoItem)));
    const duration = textOf(video.duration || video.length);
    const channel = video.channel;
    return h("article", { class: "sk-video" },
      extLink(video.link, [thumb(imageField(video), { cls: "video", alt: "", fallback: titleText(video) }), duration ? h("span", { class: "sk-duration", text: duration }) : null], { class: "sk-video-thumb", "aria-label": `Open ${titleText(video, "video")}` }),
      h("div", { class: "sk-video-body" },
        extLink(video.link, titleText(video, "Video"), { class: "sk-item-title" }),
        h("div", { class: "sk-byline", text: sourceName(channel) || textOf(video.source) }),
        metaLine(video.views !== undefined ? `${typeof video.views === "number" ? compactNumber(video.views) : video.views} views` : "", video.published_date || video.date || ""),
        video.description || video.snippet ? h("p", { class: "sk-text clamp", text: textOf(video.description || video.snippet) }) : null));
  }

  function eventItem(event) {
    const date = event.date || {};
    return h("article", { class: "sk-item event" },
      h("div", { class: "sk-item-row" },
        h("div", { class: "sk-datebox", "aria-hidden": !date.start_date }, h("b", { text: textOf(date.start_date).split(" ").pop() || "" }), h("span", { text: textOf(date.start_date).split(" ")[0] || "" })),
        h("div", { class: "sk-item-main" },
          extLink(event.link, titleText(event, "Event"), { class: "sk-item-title" }),
          h("div", { class: "sk-byline", text: textOf(date.when) }),
          metaLine(isObject(event.venue) ? event.venue.name : "", Array.isArray(event.address) ? event.address.join(", ") : textOf(event.address)),
          event.description ? h("p", { class: "sk-text clamp", text: event.description }) : null),
        imageField(event) ? thumb(imageField(event), { cls: "side", alt: "" }) : null));
  }

  function reviewItem(review) {
    const user = isObject(review.user) ? review.user : {};
    const name = textOf(user.name || review.author || review.reviewer || review.username || (isObject(review.user_name) ? "" : review.user_name) || review.name);
    const body = textOf(review.snippet || review.text || (isObject(review.comment) ? review.comment.text : review.comment) || review.content || review.body || review.description || review.extracted_snippet);
    const likes = review.likes !== undefined ? review.likes : review.helpful_votes;
    const response = review.response;
    const images = Array.isArray(review.images) ? review.images : [];
    return h("article", { class: "sk-item review" },
      h("div", { class: "sk-review-head" }, thumb(user.thumbnail || review.avatar, { cls: "avatar", alt: "", fallback: name }),
        h("div", {}, h("strong", { text: name || "Anonymous" }), metaLine(user.local_guide ? "Local Guide" : "", user.reviews ? `${user.reviews} reviews` : "", review.date || review.iso_date || review.published_date)),
        stars(review.rating)),
      review.title ? h("div", { class: "sk-item-title plain", text: review.title }) : null,
      body ? h("p", { class: "sk-text", text: body }) : null,
      images.length ? h("div", { class: "sk-thumbs" }, images.slice(0, 5).map((i) => thumb(typeof i === "string" ? i : i.thumbnail || i.image, { cls: "mini", alt: "" }))) : null,
      likes ? chipRow([`${likes} found this helpful`]) : null,
      response && (response.snippet || response.text) ? h("blockquote", { class: "sk-response" }, h("strong", { text: "Owner response" }), h("p", { text: textOf(response.snippet || response.text) })) : null);
  }

  // ---- AI answers, answer box, knowledge graph --------------------------------------------------------------------------

  /** Small safe markdown: headings, lists, quotes, fences, bold, italics, inline code and https links. DOM nodes only. */
  function markdownNode(source) {
    const out = h("div", { class: "sk-md" });
    const inline = (text) => {
      const nodes = [];
      const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|(?<![*\w])\*[^*\s][^*]*\*(?!\*)|(?<![\w])_[^_\s][^_]*_(?![\w]))/g;
      let last = 0;
      for (const match of text.matchAll(pattern)) {
        if (match.index > last) nodes.push(text.slice(last, match.index));
        const token = match[0];
        if (token.startsWith("**")) nodes.push(h("strong", { text: token.slice(2, -2) }));
        else if (token.startsWith("`")) nodes.push(h("code", { text: token.slice(1, -1) }));
        else if (token.startsWith("[")) { const m = token.match(/^\[([^\]]+)\]\((.+)\)$/); nodes.push(extLink(m[2], m[1])); }
        else nodes.push(h("em", { text: token.slice(1, -1) }));
        last = match.index + token.length;
      }
      if (last < text.length) nodes.push(text.slice(last));
      return nodes;
    };
    const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
    let paragraph = [];
    let list = null;
    const flushParagraph = () => { if (paragraph.length) { out.append(h("p", {}, inline(paragraph.join(" ")))); paragraph = []; } };
    const flushList = () => { list = null; };
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      if (/^```/.test(line)) {
        flushParagraph(); flushList();
        const code = [];
        i += 1;
        while (i < lines.length && !/^```/.test(lines[i])) { code.push(lines[i]); i += 1; }
        out.append(h("pre", { class: "sk-md-code" }, h("code", { text: code.join("\n") })));
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (heading) { flushParagraph(); flushList(); out.append(h(`h${Math.min(6, heading[1].length + 2)}`, {}, inline(heading[2]))); }
      else if (bullet || ordered) {
        flushParagraph();
        const kind = bullet ? "ul" : "ol";
        if (!list || list.tagName.toLowerCase() !== kind) { list = h(kind, {}); out.append(list); }
        list.append(h("li", {}, inline((bullet || ordered)[1])));
      } else if (/^>\s?/.test(line)) { flushParagraph(); flushList(); out.append(h("blockquote", {}, inline(line.replace(/^>\s?/, "")))); }
      else if (!line.trim()) { flushParagraph(); flushList(); }
      else { flushList(); paragraph.push(line.trim()); }
    }
    flushParagraph();
    return out;
  }

  function aiBlocksNode(blocks, references) {
    const refs = Array.isArray(references) ? references : [];
    const prefix = uid("ref");
    const refNodes = refs.map((ref, index) => h("li", { id: `${prefix}-${index}`, tabindex: "-1", class: "sk-ref-item" },
      h("div", {}, extLink(ref.link, ref.title || ref.link || `Source ${index + 1}`, { class: "sk-item-title" })),
      h("div", { class: "sk-byline", text: [ref.source, hostOf(ref.link)].filter(Boolean).join(" · ") }),
      ref.snippet ? h("p", { class: "sk-text clamp", text: ref.snippet }) : null));
    const cites = (indexes) => (Array.isArray(indexes) && indexes.length ? h("span", { class: "sk-cites" }, indexes.map((n) => h("button", {
      type: "button", class: "sk-cite", "aria-label": `Source ${Number(n) + 1}`, text: String(Number(n) + 1),
      onclick: () => { const target = document.getElementById(`${prefix}-${n}`); if (target) { target.scrollIntoView({ block: "center", behavior: "smooth" }); target.focus({ preventScroll: true }); target.classList.add("flash"); setTimeout(() => target.classList.remove("flash"), 1400); } },
    }))) : null);
    const listItems = (items, ordered) => h(ordered ? "ol" : "ul", {}, (items || []).map((item) => (isObject(item)
      ? h("li", {}, item.title ? h("strong", { text: `${item.title} ` }) : null, item.snippet ? highlighted(item.snippet, item.snippet_highlighted_words) : null, cites(item.reference_indexes), Array.isArray(item.list) && item.list.length ? listItems(item.list, false) : null)
      : h("li", { text: textOf(item) }))));
    const tableNode = (rows) => h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, (rows[0] || []).map((c) => h("th", { text: textOf(c) })))), h("tbody", {}, rows.slice(1).map((r) => h("tr", {}, r.map((c) => h("td", { text: textOf(c) })))))));
    const body = (Array.isArray(blocks) ? blocks : []).map((block) => {
      if (!isObject(block)) return h("p", { text: textOf(block) });
      switch (block.type) {
        case "heading": return h("h4", { class: "sk-ai-h" }, block.snippet || block.title || "");
        case "list": return listItems(block.list, block.list_type === "ordered" || block.ordered);
        case "table": return Array.isArray(block.table) && block.table.every(Array.isArray) ? tableNode(block.table) : block.formatted ? h("pre", { class: "sk-md-code", text: textOf(block.formatted) }) : null;
        case "code_block": return h("pre", { class: "sk-md-code" }, h("code", { text: textOf(block.code || block.snippet) }));
        default: return h("p", { class: "sk-ai-p" }, highlighted(block.snippet || block.text || textOf(block), block.snippet_highlighted_words), cites(block.reference_indexes));
      }
    });
    return h("div", { class: "sk-ai" }, h("div", { class: "sk-ai-text" }, body), refNodes.length ? h("aside", { class: "sk-ai-refs", "aria-label": "Sources" }, h("h4", { text: "Sources" }), h("ol", {}, refNodes)) : null);
  }

  function aiOverviewNode(value, ctx) {
    if (Array.isArray(value.text_blocks) && value.text_blocks.length) return aiBlocksNode(value.text_blocks, value.references);
    if (value.page_token || value.serpapi_link) return note("Google returned an AI overview that needs a follow-up request. Run the google_ai_overview engine with this page token to read it.", "");
    if (value.error) return note(textOf(value.error), "warn");
    return jsonTree(value, { maxDepth: 1, search: false, label: "AI overview" });
  }

  function answerBoxNode(box) {
    const lead = pick(box, "answer", "result", "snippet", "title", "temperature");
    const rest = { ...box };
    for (const k of ["answer", "result", "snippet", "title", "snippet_highlighted_words", "list", "table", "link", "displayed_link", "type", "images", "contents", "highlighted_words"]) delete rest[k];
    const table = Array.isArray(box.table) && box.table.every(Array.isArray) ? box.table : null;
    return h("div", { class: "sk-answer" },
      box.type ? chip(humanize(box.type), "tone") : null,
      box.title && lead !== box.title ? h("div", { class: "sk-answer-title", text: box.title }) : null,
      lead ? h("div", { class: typeof lead === "string" && lead.length > 90 ? "sk-answer-text" : "sk-answer-big" }, highlighted(textOf(lead), box.snippet_highlighted_words || box.highlighted_words)) : null,
      Array.isArray(box.list) ? h("ul", { class: "sk-answer-list" }, box.list.map((i) => h("li", { text: textOf(i) }))) : null,
      table ? h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("tbody", {}, table.map((r) => h("tr", {}, r.map((c) => h("td", { text: textOf(c) })))))) ) : null,
      box.link ? h("div", { class: "sk-byline" }, extLink(box.link, box.displayed_link || box.link)) : null,
      kvList(rest, { max: 10 }));
  }

  function knowledgeGraphNode(kg) {
    const stats = kg.key_stats && Array.isArray(kg.key_stats.stats) ? kg.key_stats.stats : [];
    const tags = kg.key_stats && Array.isArray(kg.key_stats.tags) ? kg.key_stats.tags : [];
    const used = new Set(["title", "type", "description", "source", "header_images", "thumbnail", "image", "kgmid", "knowledge_graph_search_link", "serpapi_knowledge_graph_search_link", "key_stats", "entity_type", "link", "website", "profiles", "people_also_search_for"]);
    const facts = [];
    const linkGroups = [];
    for (const [key, value] of Object.entries(kg)) {
      if (used.has(key) || isEmpty(value)) continue;
      if (typeof value === "string" || typeof value === "number") { if (!key.endsWith("_link") && !key.endsWith("_links") && !key.endsWith("_stick")) facts.push([key, value]); }
      else if (Array.isArray(value) && value.length && value.every((v) => isObject(v) && (v.name || v.title || v.text))) linkGroups.push([key, value]);
      else if (Array.isArray(value) && value.every((v) => typeof v !== "object")) facts.push([key, value.join(", ")]);
    }
    const image = imageField(kg, "header_images", "thumbnail");
    const profiles = Array.isArray(kg.profiles) ? kg.profiles : [];
    return h("div", { class: "sk-kg" },
      h("div", { class: "sk-kg-head" }, image ? thumb(image, { cls: "kg", alt: "", fallback: kg.title }) : null,
        h("div", {}, h("div", { class: "sk-answer-title", text: kg.title || "Knowledge graph" }), kg.type || kg.entity_type ? h("div", { class: "sk-byline", text: kg.type || kg.entity_type }) : null,
          kg.website || kg.link ? h("div", { class: "sk-byline" }, extLink(kg.website || kg.link, hostOf(kg.website || kg.link) || kg.website || kg.link)) : null)),
      kg.description ? h("p", { class: "sk-text", text: kg.description }, kg.source && kg.source.link ? [" ", extLink(kg.source.link, kg.source.name || "Source")] : null) : null,
      tags.length ? chipRow(tags.map((t) => textOf(t))) : null,
      stats.length ? h("div", { class: "sk-stats" }, stats.map((s) => h("div", { class: "sk-stat" }, h("span", { class: "sk-stat-label", text: s.label || "" }), h("b", { text: textOf(s.value) }), s.description ? h("small", { text: String(s.description).slice(0, 140) }) : null))) : null,
      facts.length ? h("dl", { class: "sk-kv" }, facts.slice(0, 18).flatMap(([k, v]) => [h("dt", { text: humanize(k) }), h("dd", { text: String(v) })])) : null,
      ...linkGroups.slice(0, 4).map(([k, list]) => h("div", { class: "sk-subsection" }, h("h4", { text: humanize(k) }), chipRow(list.slice(0, 12).map((i) => extLink(i.link, textOf(i.name || i.title || i.text), { class: "sk-chip link" }) )))),
      profiles.length ? h("div", { class: "sk-links" }, profiles.map((p) => extLink(p.link, p.name, { class: "sk-linkbtn" }))) : null);
  }

  function questionsNode(list) {
    return h("div", { class: "sk-accordion" }, list.map((q) => h("details", {}, h("summary", {}, h("span", { text: q.question || titleText(q, "Question") })),
      h("div", { class: "sk-acc-body" },
        q.snippet || q.answer ? h("p", { class: "sk-text", text: textOf(q.snippet || q.answer) }) : null,
        Array.isArray(q.list) ? h("ul", {}, q.list.map((i) => h("li", { text: textOf(i) }))) : null,
        q.link ? h("div", { class: "sk-byline" }, extLink(q.link, q.title || q.displayed_link || q.link)) : null,
        q.date ? h("div", { class: "sk-dim", text: q.date }) : null))));
  }

  function chipsNode(list) {
    return h("div", { class: "sk-chips big" }, list.slice(0, 80).map((item) => {
      const text = typeof item === "string" ? item : textOf(pick(item, "query", "value", "text", "title", "name", "keyword", "label", "topic") || "");
      if (!text) return null;
      const extra = isObject(item) ? (item.mentions !== undefined ? `${item.mentions}` : item.relevance !== undefined ? "" : "") : "";
      const link = isObject(item) ? safeLink(item.link) : null;
      const children = [h("span", { text }), extra ? h("small", { text: extra }) : null];
      return link ? h("a", { class: "sk-chip link", href: link, target: "_blank", rel: "noopener noreferrer" }, children) : h("span", { class: "sk-chip" }, children);
    }));
  }

  function filtersNode(list) {
    return h("div", { class: "sk-chips big" }, list.map((f) => h("span", { class: "sk-chip", title: f.parameters ? Object.entries(f.parameters).map(([k, v]) => `${k}=${String(v).slice(0, 40)}`).join("  ") : null },
      h("span", { text: f.name || f.text || "Filter" }), Array.isArray(f.options) ? h("small", { text: `${f.options.length}` }) : null)));
  }

  // ---- travel ------------------------------------------------------------------------------------------------------------------------

  function clockParts(value) {
    const match = String(value || "").match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (!match) return { time: textOf(value), date: "" };
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5]));
    return { time: date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }), date: date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }), key: `${match[1]}-${match[2]}-${match[3]}` };
  }

  function flightNode(option, currency) {
    const segments = Array.isArray(option.flights) ? option.flights : [];
    const layovers = Array.isArray(option.layovers) ? option.layovers : [];
    const first = segments[0] || {};
    const last = segments[segments.length - 1] || {};
    const logos = [...new Set(segments.map((s) => s.airline_logo).filter(Boolean))].slice(0, 2);
    const airlines = [...new Set(segments.map((s) => s.airline).filter(Boolean))];
    const carbon = option.carbon_emissions;
    const stops = layovers.length;
    const totalDuration = formatDuration(option.total_duration);
    const departure = clockParts(first.departure_airport && first.departure_airport.time);
    const arrival = clockParts(last.arrival_airport && last.arrival_airport.time);
    const line = h("div", { class: "sk-leg-line" }, segments.map((segment, index) => {
      const dep = clockParts(segment.departure_airport && segment.departure_airport.time);
      const arr = clockParts(segment.arrival_airport && segment.arrival_airport.time);
      const layover = layovers[index];
      return h("div", { class: "sk-leg" },
        h("div", { class: "sk-leg-grid" },
          h("div", { class: "sk-leg-end" }, h("b", { text: dep.time }), h("span", { text: [segment.departure_airport && segment.departure_airport.id, segment.departure_airport && segment.departure_airport.name].filter(Boolean).join(" · ") })),
          h("div", { class: "sk-leg-mid", "aria-hidden": "true" }, h("span", { text: formatDuration(segment.duration) }), h("i")),
          h("div", { class: "sk-leg-end right" }, h("b", { text: arr.time + (arr.key && dep.key && arr.key !== dep.key ? " +1" : "") }), h("span", { text: [segment.arrival_airport && segment.arrival_airport.id, segment.arrival_airport && segment.arrival_airport.name].filter(Boolean).join(" · ") }))),
        metaLine(segment.airline, segment.flight_number, segment.airplane, segment.travel_class, segment.legroom),
        chipRow([...(segment.extensions || []).slice(0, 3), segment.often_delayed_by_over_30_min ? "Often delayed" : ""].filter(Boolean)),
        layover && index < segments.length - 1 ? h("div", { class: "sk-layover" }, icon("plane"), h("span", { text: `Layover ${formatDuration(layover.duration)} · ${layover.name || layover.id || ""}` }), layover.overnight ? chip("Overnight", "warn") : null) : null);
    }));
    return h("article", { class: "sk-flight" },
      h("header", { class: "sk-flight-head" },
        h("div", { class: "sk-logos" }, logos.map((l) => thumb(l, { cls: "airline", alt: "" })), logos.length ? null : thumb("", { cls: "airline", alt: "", fallback: airlines[0] })),
        h("div", { class: "sk-flight-main" },
          h("div", { class: "sk-flight-times" }, h("b", { text: `${departure.time} – ${arrival.time}` }), departure.date ? h("span", { class: "sk-dim", text: departure.date }) : null),
          h("div", { class: "sk-byline", text: airlines.join(", ") })),
        h("div", { class: "sk-flight-sum" }, totalDuration ? h("b", { text: totalDuration }) : null, h("span", { class: "sk-dim", text: stops === 0 ? "Nonstop" : `${stops} stop${stops === 1 ? "" : "s"}` })),
        h("div", { class: "sk-flight-price" }, option.price !== undefined ? h("b", { text: moneyText(option.price, currency) }) : null, option.type ? h("span", { class: "sk-dim", text: option.type }) : null)),
      chipRow([carbon && carbon.this_flight ? `${Math.round(carbon.this_flight / 1000)} kg CO2e${carbon.difference_percent !== undefined ? ` · ${carbon.difference_percent > 0 ? "+" : ""}${carbon.difference_percent}% vs typical` : ""}` : "", ...(option.extensions || []).slice(0, 3)].filter(Boolean)),
      h("details", { class: "sk-leg-details" }, h("summary", { text: segments.length > 1 ? `${segments.length} flights, ${stops} layover${stops === 1 ? "" : "s"}` : "Flight details" }), line));
  }

  function priceInsightsNode(insights, currency) {
    const history = Array.isArray(insights.price_history) ? insights.price_history.filter((p) => Array.isArray(p) && typeof p[1] === "number") : [];
    const range = Array.isArray(insights.typical_price_range) ? insights.typical_price_range : null;
    const level = insights.price_level;
    return h("div", { class: "sk-insights" },
      h("div", { class: "sk-stats" },
        insights.lowest_price !== undefined ? h("div", { class: "sk-stat" }, h("span", { class: "sk-stat-label", text: "Lowest price" }), h("b", { text: moneyText(insights.lowest_price, currency) })) : null,
        level ? h("div", { class: "sk-stat" }, h("span", { class: "sk-stat-label", text: "Price level" }), h("b", { text: humanize(level) })) : null,
        range ? h("div", { class: "sk-stat" }, h("span", { class: "sk-stat-label", text: "Typical range" }), h("b", { text: `${moneyText(range[0], currency)} – ${moneyText(range[1], currency)}` })) : null),
      history.length > 1 ? lineChart({ series: [{ name: "Price", values: history.map((p) => p[1]) }], labels: history.map((p) => new Date(p[0] * 1000).toLocaleDateString([], { month: "short", day: "numeric" })), area: true, format: (v) => moneyText(Math.round(v), currency), ariaLabel: "Price history", height: 180 }) : null);
  }

  function hotelNode(p, currency) {
    const rate = p.rate_per_night || {};
    const total = p.total_rate || {};
    const nightly = rate.lowest || (p.price ? textOf(p.price) : "") || (p.extracted_price !== undefined ? moneyText(p.extracted_price, currency) : "");
    const amenities = Array.isArray(p.amenities) ? p.amenities : [];
    const nearby = Array.isArray(p.nearby_places) ? p.nearby_places : [];
    const providers = Array.isArray(p.prices) ? p.prices : [];
    const hotelClass = p.extracted_hotel_class || parseNumber(p.hotel_class);
    return h("article", { class: "sk-hotel" },
      thumb(imageField(p, "images"), { cls: "hotel", alt: p.name || "", fallback: p.name }),
      h("div", { class: "sk-hotel-body" },
        h("div", { class: "sk-hotel-top" },
          h("div", {}, extLink(p.link, p.name || p.title || "Property", { class: "sk-item-title" }),
            h("div", { class: "sk-byline" }, [p.type, typeof p.hotel_class === "string" && p.hotel_class ? p.hotel_class : hotelClass ? `${hotelClass}-star` : ""].filter(Boolean).join(" · "))),
          h("div", { class: "sk-hotel-price" }, nightly ? h("b", { text: nightly }) : null, nightly ? h("span", { class: "sk-dim", text: "per night" }) : null, total.lowest ? h("span", { class: "sk-dim", text: `${total.lowest} total` }) : null)),
        h("div", { class: "sk-hotel-rating" }, stars(p.overall_rating || p.rating, { count: p.reviews }), p.location_rating ? chip(`Location ${p.location_rating}`) : null, p.deal || p.deal_description ? chip(p.deal || p.deal_description, "good") : null),
        metaLine(p.check_in_time ? `Check-in ${p.check_in_time}` : "", p.check_out_time ? `Check-out ${p.check_out_time}` : "", p.description),
        chipRow([...amenities.slice(0, 7), amenities.length > 7 ? `+${amenities.length - 7} more` : ""].filter(Boolean)),
        nearby.length ? h("div", { class: "sk-dim", text: nearby.slice(0, 3).map((n) => `${n.name}${n.transportations && n.transportations[0] ? ` · ${n.transportations[0].duration}` : ""}`).join("   ") }) : null,
        providers.length ? h("details", { class: "sk-leg-details" }, h("summary", { text: `${providers.length} booking options` }),
          h("ul", { class: "sk-providers" }, providers.map((pr) => h("li", {}, h("span", { text: pr.source }), h("b", { text: textOf(pr.rate_per_night && pr.rate_per_night.lowest ? pr.rate_per_night.lowest : pr.rate_per_night) }))))) : null));
  }

  // ---- finance, trends --------------------------------------------------------------------------------------------------

  function movementNode(movement) {
    if (!isObject(movement)) return null;
    const down = /down/i.test(movement.movement || "") || Number(movement.percentage) < 0;
    return h("span", { class: `sk-move ${down ? "down" : "up"}` }, h("span", { "aria-hidden": "true", text: down ? "▼" : "▲" }),
      h("span", { text: [movement.percentage !== undefined ? `${Math.abs(Number(movement.percentage))}%` : "", movement.value !== undefined ? `(${Math.abs(Number(movement.value))})` : ""].filter(Boolean).join(" ") }),
      h("span", { class: "visually-hidden", text: down ? " down" : " up" }));
  }

  function financeNode(result) {
    const s = result.summary || {};
    const graph = Array.isArray(result.graph) ? result.graph : [];
    const prices = graph.map((p) => (typeof p.price === "number" ? p.price : parseNumber(p.price)));
    const currency = s.currency || (graph[0] && graph[0].currency) || "USD";
    return h("div", { class: "sk-finance" },
      h("header", { class: "sk-quote" },
        h("div", {}, h("div", { class: "sk-answer-title", text: s.title || s.stock || "Quote" }), h("div", { class: "sk-byline", text: [s.stock, s.exchange, s.market].filter(Boolean).join(" · ") })),
        h("div", { class: "sk-quote-price" }, h("b", { text: textOf(s.price !== undefined ? s.price : s.extracted_price) }), movementNode(s.price_movement), s.date ? h("span", { class: "sk-dim", text: s.date }) : null)),
      prices.some((p) => p !== null) ? lineChart({ series: [{ name: s.stock || "Price", values: prices }], labels: graph.map((p) => p.date || ""), area: true, format: (v) => moneyText(Math.round(v * 100) / 100, currency), ariaLabel: `${s.title || "Price"} chart`, height: 240, tableLabel: "Time" }) : null);
  }

  function marketTrendsNode(groups) {
    return h("div", { class: "sk-subsections" }, groups.map((group) => h("div", { class: "sk-subsection" }, h("h4", { text: group.title || "Markets" }),
      h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("tbody", {}, (group.results || []).map((r) => h("tr", {},
        h("td", {}, extLink(r.link, r.name || r.stock || "")), h("td", { class: "sk-dim", text: r.stock || "" }), h("td", { class: "num", text: textOf(r.price !== undefined ? r.price : r.extracted_price) }), h("td", { class: "num" }, movementNode(r.price_movement))))))))));
  }

  function trendsTimelineNode(value) {
    const data = Array.isArray(value.timeline_data) ? value.timeline_data : [];
    const names = [];
    for (const row of data) (row.values || []).forEach((v, i) => { const name = v.query || `Term ${(v.query_index !== undefined ? v.query_index : i) + 1}`; if (!names.includes(name)) names.push(name); });
    const series = names.map((name) => ({ name, values: data.map((row) => { const found = (row.values || []).find((v, i) => (v.query || `Term ${(v.query_index !== undefined ? v.query_index : i) + 1}`) === name); return found ? (typeof found.extracted_value === "number" ? found.extracted_value : parseNumber(found.value)) : null; }) }));
    const averages = Array.isArray(value.averages) ? value.averages : [];
    return h("div", {},
      lineChart({ series, labels: data.map((r) => r.date || ""), yMin: 0, yMax: 100, height: 260, ariaLabel: `Search interest over time for ${names.join(", ")}`, format: (v) => String(Math.round(v)) }),
      averages.length ? h("div", { class: "sk-chips" }, averages.map((a) => chip(`${a.query}: average ${a.value}`))) : null);
  }

  function regionBarsNode(list) {
    const rows = list.map((r) => ({
      label: textOf(r.location || r.geo || r.name || r.country || "Region"),
      values: Array.isArray(r.values) && r.values.length ? r.values.map((v) => (typeof v.extracted_value === "number" ? v.extracted_value : parseNumber(v.value))) : [typeof r.extracted_value === "number" ? r.extracted_value : parseNumber(r.value)],
    })).sort((a, b) => (b.values[0] || 0) - (a.values[0] || 0)).slice(0, 25);
    return barRows(rows, { max: 100 });
  }

  function relatedTrendsNode(value) {
    const lists = Array.isArray(value) ? [["Related", value]] : [["Rising", value.rising], ["Top", value.top]].filter(([, v]) => Array.isArray(v) && v.length);
    if (!lists.length) return jsonTree(value, { maxDepth: 1, search: false });
    return h("div", { class: "sk-two" }, lists.map(([name, items]) => h("div", {}, h("h4", { text: name }),
      h("ol", { class: "sk-rank" }, items.slice(0, 15).map((item) => {
        const label = textOf(item.query || (isObject(item.topic) ? item.topic.title : item.topic) || item.title || item.name);
        const type = isObject(item.topic) ? item.topic.type : "";
        const rising = /breakout/i.test(String(item.value)) || /%/.test(String(item.value));
        return h("li", {}, extLink(item.link, label), type ? h("span", { class: "sk-dim", text: type }) : null, h("b", { class: rising ? "sk-move up" : "", text: textOf(item.value !== undefined ? item.value : item.extracted_value) }));
      })))));
  }

  function trendingNowNode(list) {
    return paged(list, (item) => h("article", { class: "sk-item trending" },
      h("div", { class: "sk-item-row" },
        h("div", { class: "sk-item-main" }, h("div", { class: "sk-item-title plain", text: textOf(item.query || item.title) }),
          chipRow([...(Array.isArray(item.categories) ? item.categories.map((c) => textOf(c)) : []), ...(Array.isArray(item.trend_breakdown) ? item.trend_breakdown.slice(0, 5) : [])]),
          metaLine(item.start_timestamp ? `Started ${new Date(item.start_timestamp * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : "")),
        h("div", { class: "sk-trend-num" }, item.search_volume !== undefined ? h("b", { text: compactNumber(item.search_volume) }) : null, item.increase_percentage !== undefined ? h("span", { class: "sk-move up" }, h("span", { "aria-hidden": "true", text: "▲" }), h("span", { text: `${item.increase_percentage}%` })) : null))));
  }

  function citedByNode(value) {
    const table = Array.isArray(value.table) ? value.table : [];
    const graph = Array.isArray(value.graph) ? value.graph : [];
    const rows = table.map((row) => { const key = Object.keys(row)[0]; const cell = row[key] || {}; return [humanize(key), cell.all, cell.since_2019 !== undefined ? cell.since_2019 : cell.since]; });
    const top = Math.max(...graph.map((g) => g.citations || 0), 1);
    return h("div", {},
      rows.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Metric" }), h("th", { class: "num", text: "All" }), h("th", { class: "num", text: "Recent" }))),
        h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { text: r[0] }), h("td", { class: "num", text: textOf(r[1]) }), h("td", { class: "num", text: textOf(r[2]) })))))) : null,
      graph.length ? h("div", { class: "sk-columns", role: "img", "aria-label": `Citations per year: ${graph.map((g) => `${g.year} ${g.citations}`).join(", ")}` },
        graph.map((g) => h("div", { class: "sk-column" }, h("span", { class: "sk-column-n", text: compactNumber(g.citations || 0) }), h("i", { style: { height: `${Math.max(2, ((g.citations || 0) / top) * 100)}%` } }), h("span", { class: "sk-column-l", text: String(g.year) })))) : null);
  }

  /** google_jobs_listing style ratings: one entry per source (company_name, source, rating, reviews, link), not a star histogram. */
  function sourceRatingsNode(list) {
    return paged(list, (item) => h("article", { class: "sk-product" },
      h("div", { class: "sk-product-body" },
        extLink(item.link, textOf(item.source || item.company_name || item.title) || "Rating source", { class: "sk-item-title" }),
        item.company_name && item.source ? h("div", { class: "sk-byline", text: textOf(item.company_name) }) : null,
        stars(item.rating, { count: item.reviews || item.rating_count }) || (isEmpty(item.rating) ? null : h("div", { class: "sk-dim", text: `Rating ${textOf(item.rating)}` })))));
  }

  function ratingBarsNode(list) {
    if (!list.some((r) => isObject(r) && r.stars !== undefined && r.stars !== null)) return sourceRatingsNode(list.filter(isObject));
    const rows = list.filter((r) => isObject(r) && r.stars !== undefined && r.stars !== null).map((r) => ({ label: `${r.stars} star${Number(r.stars) === 1 ? "" : "s"}`, value: r.count || r.amount || 0, stars: Number(r.stars) })).sort((a, b) => b.stars - a.stars);
    return barRows(rows);
  }

  function transcriptNode(list) {
    return paged(list, (row) => h("div", { class: "sk-transcript-row" }, h("time", { text: row.start_time_text || row.time || (row.start_ms !== undefined ? formatDuration(row.start_ms / 60000) || "0m" : "") }), h("span", { text: textOf(row.snippet || row.title || row.text) })), { pageSize: 60, cls: "sk-transcript" });
  }

  function placeCardNode(place) {
    const gps = place.gps_coordinates;
    const map = osmLink(gps);
    const hours = Array.isArray(place.hours) ? place.hours : Array.isArray(place.operating_hours) ? place.operating_hours : isObject(place.operating_hours) ? Object.entries(place.operating_hours).map(([k, v]) => ({ [k]: v })) : [];
    const options = isObject(place.service_options) ? Object.entries(place.service_options).filter(([, v]) => v).map(([k]) => humanize(k)) : [];
    const images = Array.isArray(place.images) ? place.images : [];
    return h("div", { class: "sk-placecard" },
      images.length ? h("div", { class: "sk-thumbs wide" }, images.slice(0, 5).map((i) => thumb(typeof i === "string" ? i : i.thumbnail || i.image, { cls: "gallery", alt: "" }))) : imageField(place) ? thumb(imageField(place), { cls: "gallery", alt: "" }) : null,
      h("div", { class: "sk-answer-title", text: place.title || "Place" }),
      stars(place.rating, { count: place.reviews }),
      metaLine(textOf(place.type), place.price ? textOf(place.price) : "", place.address),
      chipRow(options),
      h("dl", { class: "sk-kv" }, [["Phone", place.phone], ["Website", place.website && extLink(place.website, hostOf(place.website) || place.website)], ["Description", textOf(place.description)], ["Plus code", place.plus_code]]
        .filter(([, v]) => !isEmpty(v)).flatMap(([k, v]) => [h("dt", { text: k }), h("dd", {}, v)])),
      hours.length ? h("details", { class: "sk-leg-details" }, h("summary", { text: "Opening hours" }), h("dl", { class: "sk-kv" }, hours.flatMap((row) => Object.entries(row).flatMap(([day, time]) => [h("dt", { text: humanize(day) }), h("dd", { text: textOf(time) })])))) : null,
      map ? h("div", { class: "sk-links" }, extLink(map, [icon("map"), "Open map"], { class: "sk-linkbtn" })) : null);
  }

  function productNode(product) {
    const price = pick(product, "price", "extracted_price");
    const media = Array.isArray(product.media) ? product.media.filter((m) => m && (m.link || m.thumbnail)) : [];
    return h("div", { class: "sk-productcard" },
      h("div", { class: "sk-kg-head" }, imageField(product, "thumbnails", "media") ? thumb(imageField(product, "thumbnails", "media"), { cls: "kg", alt: "" }) : null,
        h("div", {}, h("div", { class: "sk-answer-title", text: product.title || "Product" }), stars(product.rating, { count: product.reviews }), price ? h("div", { class: "sk-price" }, h("b", { text: textOf(price) })) : null,
          product.author || product.developer ? h("div", { class: "sk-byline", text: textOf(product.author || product.developer) }) : null)),
      product.description ? h("p", { class: "sk-text", text: textOf(product.description) }) : null,
      media.length > 1 ? h("div", { class: "sk-thumbs wide" }, media.slice(0, 6).map((m) => thumb(m.thumbnail || m.link, { cls: "gallery", alt: "" }))) : null,
      kvList(product, { max: 12, skip: ["title", "description", "rating", "reviews", "price", "media", "thumbnails", "thumbnail"] }));
  }

  // ---- renderer table and the visual view ---------------------------------------------------------------------------

  const currencyOf = (ctx) => (ctx.result.search_parameters && ctx.result.search_parameters.currency) || "USD";
  const RENDERERS = {
    aiOverview: (v, ctx) => aiOverviewNode(v, ctx),
    aiBlocks: (v, ctx) => aiBlocksNode(v, ctx.result.references),
    markdown: (v) => markdownNode(textOf(v)),
    answerBox: (v) => answerBoxNode(v),
    knowledgeGraph: (v) => knowledgeGraphNode(v),
    finance: (v, ctx) => financeNode(ctx.result),
    marketTrends: (v) => marketTrendsNode(v),
    product: (v) => productNode(v),
    place: (v) => placeCardNode(v),
    trendsTimeline: (v) => trendsTimelineNode(v),
    regionBars: (v) => regionBarsNode(sameList(v, "regions")),
    trendingNow: (v) => trendingNowNode(v),
    priceInsights: (v, ctx) => priceInsightsNode(v, currencyOf(ctx)),
    flights: (v, ctx) => paged(v, (o) => flightNode(o, currencyOf(ctx)), { pageSize: 8 }),
    hotels: (v, ctx) => paged(v, (p) => hotelNode(p, currencyOf(ctx)), { pageSize: 10 }),
    jobs: (v) => paged(v, jobItem, { pageSize: 15 }),
    organic: (v) => paged(v, organicItem, { pageSize: 20 }),
    places: (v) => paged(sameList(v, "places"), placeItem, { pageSize: 15 }),
    shopping: (v) => paged(sameList(v, "shopping_results", "items"), shoppingItem, { pageSize: 24, cls: "sk-grid-cards products" }),
    news: (v) => paged(sameList(v, "news"), newsItem, { pageSize: 15 }),
    images: (v) => paged(sameList(v, "images"), imageTile, { pageSize: 36, cls: "sk-gallery" }),
    videos: (v) => paged(sameList(v, "videos"), videoItem, { pageSize: 12, cls: "sk-grid-cards videos" }),
    events: (v) => paged(v, eventItem, { pageSize: 12 }),
    ratingBars: (v) => ratingBarsNode(v),
    reviews: (v, ctx) => h("div", {},
      ctx.result.place_info && ctx.result.place_info.title ? h("div", { class: "sk-placeinfo" }, h("strong", { text: ctx.result.place_info.title }), stars(ctx.result.place_info.rating, { count: ctx.result.place_info.reviews }), metaLine(ctx.result.place_info.address, ctx.result.place_info.type)) : null,
      paged(sameList(v, "reviews"), reviewItem, { pageSize: 12 })),
    citedBy: (v) => citedByNode(v),
    questions: (v) => questionsNode(v),
    chips: (v) => chipsNode(v),
    relatedTrends: (v) => relatedTrendsNode(v),
    filters: (v) => filtersNode(v),
    transcript: (v) => transcriptNode(v),
    cards: (v) => paged(v, genericCard, { pageSize: 15 }),
  };

  function sectionBody(entry, ctx) {
    const value = ctx.result[entry.key];
    try {
      const render = RENDERERS[entry.renderer];
      if (render) return render(value, ctx);
    } catch (error) {
      console.error("serp kit: renderer failed", entry.key, error);
      return h("div", {}, note(`This section could not be drawn (${error && error.message ? error.message : "unexpected shape"}). The data is shown as JSON instead.`, "warn"), jsonTree(value, { maxDepth: 1, search: false }));
    }
    return jsonTree(value, { maxDepth: 1, search: false, label: titleForKey(entry.key) });
  }

  function infoStrip(result, record) {
    const info = isObject(result.search_information) ? result.search_information : {};
    const parts = [];
    if (info.total_results !== undefined) parts.push(`${fmtNumber(info.total_results)} results`);
    if (info.time_taken_displayed !== undefined) parts.push(`${info.time_taken_displayed}s on the engine`);
    if (info.organic_results_state) parts.push(String(info.organic_results_state).replaceAll("_", " "));
    const corrected = info.spelling_fix || info.showing_results_for;
    if (!info.query_displayed && !parts.length && !record.cache_hit && !corrected) return null;
    return h("div", { class: "sk-strip" },
      engineBadge({ id: record.engine, name: record.engine_name || humanize(record.engine || "") }),
      info.query_displayed ? h("span", { class: "sk-strip-q" }, "for ", h("b", { text: info.query_displayed })) : null,
      parts.length ? h("span", { class: "sk-dim", text: parts.join(" · ") }) : null,
      record.cache_hit ? creditChip(record) : null,
      corrected ? h("span", { class: "sk-chip warn", text: `Showing results for ${corrected}` }) : null);
  }

  function renderVisual(record) {
    const result = record.result;
    const ctx = { record, result };
    const plan = planSections(result);
    const root = h("div", { class: "sk-visual" });
    const strip = infoStrip(result, record);
    if (strip) root.append(strip);
    const display = plan.filter((p) => p.renderer !== "tree");
    const leftover = plan.filter((p) => p.renderer === "tree");
    const pagination = result.serpapi_pagination || result.pagination;
    if (!display.length && !leftover.length) { root.append(note("The engine returned no result sections for this search.", "")); }
    const anchors = display.map((entry) => ({ entry, id: uid("sec") }));
    if (anchors.length > 3) {
      root.append(h("nav", { class: "sk-jump", "aria-label": "Jump to a result section" }, anchors.map(({ entry, id }) => h("button", {
        type: "button", class: "sk-chip link", onclick: () => { const target = document.getElementById(id); if (target) { target.scrollIntoView({ block: "start", behavior: "smooth" }); const toggle = target.querySelector(".sk-sec-toggle"); if (toggle && toggle.getAttribute("aria-expanded") !== "true") toggle.click(); } },
      }, h("span", { text: titleForKey(entry.key) }), entry.count !== null ? h("small", { text: String(entry.count) }) : null))));
    }
    for (const { entry, id } of anchors) {
      root.append(section(titleForKey(entry.key), { count: entry.count, id, body: sectionBody(entry, ctx), open: true }));
    }
    if (leftover.length) {
      const rest = Object.fromEntries(leftover.map((e) => [e.key, result[e.key]]));
      root.append(section("More data", { count: leftover.length, open: !display.length, body: jsonTree(rest, { maxDepth: 1, search: leftover.length > 2, label: "Other fields" }) }));
    }
    if (isObject(pagination) && Object.keys(pagination).length) root.append(section("Pagination", { open: false, body: kvList(pagination, { max: 20 }) || note("No pagination data.") }));
    return root;
  }

  // ===========================================================================
  // Result modes: table, raw, html, metadata, and the viewer that ties them together
  // ===========================================================================

  const ERROR_ADVICE = [
    [/invalid api key|api_key/i, "Check the SerpApi key on the Account tab."],
    [/run out of searches|out of searches|searches left/i, "The plan has no searches left. Add credits or wait for the monthly reset."],
    [/hasn't returned any results|no results/i, "The search ran but the engine found nothing. Adjust the query or location; retrying the same search will not help."],
    [/rate limit|too many|throttl|hourly/i, "Slow down: the hourly search rate was reached."],
    [/missing|required|parameter/i, "A parameter is missing or invalid. Check the form for the highlighted fields."],
  ];

  function failurePanel(record) {
    const message = record.error || (record.result && typeof record.result.error === "string" ? record.result.error : "");
    const advice = ERROR_ADVICE.find(([pattern]) => pattern.test(message));
    return h("div", { class: "sk-failure", role: "alert" },
      h("div", { class: "sk-failure-head" }, statusBadge({ ...record, status: "error" }), record.engine ? engineBadge({ id: record.engine, name: record.engine_name }) : null),
      h("p", { class: "sk-failure-msg", text: redact(message || "The search did not return a result.") }),
      advice ? h("p", { class: "sk-hint", text: advice[1] }) : null);
  }

  function cellNode(key, value) {
    if (value === "" || value === null || value === undefined) return h("span", { class: "sk-dim", text: "–" });
    if (typeof value === "string") {
      const text = redact(value);
      if (/thumbnail|image|favicon|logo|icon|photo|avatar/i.test(key) && safeImage(text)) return thumb(text, { cls: "mini", alt: "" });
      const link = safeLink(text);
      if (link && !/api_key=/i.test(text)) return extLink(link, hostOf(link) + (new URL(link).pathname.length > 1 ? new URL(link).pathname.slice(0, 28) : ""), { title: link });
      return h("span", { text, title: text.length > 60 ? text : null });
    }
    return h("span", { text: String(value) });
  }

  function renderTable(record) {
    const result = record.result;
    const candidates = listCandidates(result);
    if (!candidates.length) return note("This response has no list of records to tabulate. Use the JSON or Visual tab.", "");
    let current = findMainList(result);
    let sortKey = "";
    let sortDir = 1;
    let filterText = "";
    let limit = 100;
    let flat = null;
    const holder = h("div", { class: "sk-table-wrap" });
    const status = h("span", { class: "sk-tree-status", role: "status", "aria-live": "polite" });
    const select = candidates.length > 1 ? h("select", { "aria-label": "List to show", class: "sk-select" }, candidates.map((c) => h("option", { value: c.key, text: `${c.key} (${c.list.length})`, selected: c.key === current.key }))) : null;
    const search = h("input", { type: "search", placeholder: "Filter rows", "aria-label": "Filter rows", autocomplete: "off" });
    const load = () => { flat = flattenRows(current.list); sortKey = ""; limit = 100; };
    const rowsNow = () => {
      let rows = flat.rows;
      if (filterText) {
        const needle = filterText.toLowerCase();
        rows = rows.filter((row) => Object.values(row).some((v) => String(v).toLowerCase().includes(needle)));
      }
      if (sortKey) {
        const numeric = rows.every((r) => r[sortKey] === "" || r[sortKey] === undefined || typeof r[sortKey] === "number" || parseNumber(r[sortKey]) !== null);
        rows = [...rows].sort((a, b) => {
          const x = a[sortKey]; const y = b[sortKey];
          if (x === "" || x === undefined) return 1;
          if (y === "" || y === undefined) return -1;
          return (numeric ? (parseNumber(x) || 0) - (parseNumber(y) || 0) : String(x).localeCompare(String(y))) * sortDir;
        });
      }
      return rows;
    };
    const csvText = () => rowsToCsv(flat.columns, rowsNow());
    const draw = () => {
      const rows = rowsNow();
      const numericColumn = (key) => { const values = flat.rows.slice(0, 60).map((r) => r[key]).filter((v) => v !== "" && v !== undefined); return values.length > 0 && values.filter((v) => typeof v === "number").length / values.length > 0.7; };
      const head = h("tr", {}, flat.columns.map((column) => {
        const active = sortKey === column.key;
        return h("th", { scope: "col", class: numericColumn(column.key) ? "num" : "", "aria-sort": active ? (sortDir === 1 ? "ascending" : "descending") : "none" },
          h("button", { type: "button", class: "sk-th", title: column.key, onclick: () => { if (sortKey === column.key) sortDir = -sortDir; else { sortKey = column.key; sortDir = 1; } draw(); } }, h("span", { text: column.label }), h("span", { "aria-hidden": "true", text: active ? (sortDir === 1 ? "▲" : "▼") : "" })));
      }));
      const body = h("tbody", {}, rows.slice(0, limit).map((row) => h("tr", {}, flat.columns.map((column) => h("td", { class: numericColumn(column.key) ? "num" : "" }, cellNode(column.key, row[column.key]))))));
      status.textContent = `${rows.length}${filterText ? ` of ${flat.rows.length}` : ""} row${rows.length === 1 ? "" : "s"}${flat.hidden ? ` · ${flat.hidden} more column${flat.hidden === 1 ? "" : "s"} in JSON` : ""}`;
      holder.replaceChildren(...[
        h("div", { class: "table-wrap sk-table" }, h("table", { class: "table" }, h("thead", {}, head), body)),
        rows.length > limit ? h("div", { class: "sk-more-row" }, h("button", { type: "button", class: "btn secondary sm", text: `Show ${Math.min(100, rows.length - limit)} more (${rows.length - limit} left)`, onclick: () => { limit += 100; draw(); } })) : null,
        rows.length ? null : note("No rows match the filter.", "")].filter(Boolean));
    };
    if (select) select.addEventListener("change", () => { current = candidates.find((c) => c.key === select.value) || current; load(); draw(); });
    search.addEventListener("input", debounce(() => { filterText = search.value.trim(); limit = 100; draw(); }, 150));
    load();
    draw();
    const stamp = () => `${record.engine || "search"}-${current.key.replace(/\W+/g, "_")}`;
    return h("div", { class: "sk-tablemode" },
      h("div", { class: "sk-tree-bar" }, select, h("div", { class: "sk-searchbox" }, icon("search"), search), status,
        h("div", { class: "btn-row" }, copyButton(() => csvText(), { label: "Copy CSV", message: "CSV copied", text: true }),
          h("button", { type: "button", class: "btn secondary sm", onclick: () => downloadText(`${stamp()}.csv`, csvText(), "text/csv") }, icon("download"), h("span", { text: "Download CSV" })))),
      holder);
  }

  function renderRaw(record) {
    const fmt = Aira.fmt;
    const text = redact(JSON.stringify(record.result, null, 2) || "");
    const cap = 400_000;
    const pre = h("pre", { class: "sk-raw", tabindex: "0", "aria-label": "Raw JSON response" });
    const showMore = h("div", { class: "sk-more-row" });
    const paint = (all) => {
      pre.textContent = all || text.length <= cap ? text : text.slice(0, cap);
      showMore.replaceChildren(all || text.length <= cap ? "" : h("button", { type: "button", class: "btn secondary sm", text: `Show all (${fmt.bytes(text.length)})`, onclick: () => paint(true) }));
    };
    paint(false);
    const wrapSwitch = toggleSwitch("Wrap lines", false, (on) => pre.classList.toggle("wrap", on));
    return h("div", { class: "sk-rawmode" },
      h("div", { class: "sk-tree-bar" }, h("span", { class: "sk-tree-status", text: `${fmt.bytes(text.length)} · ${text.split("\n").length.toLocaleString()} lines` }), wrapSwitch.node,
        h("div", { class: "btn-row" }, copyButton(text, { label: "Copy JSON", message: "JSON copied", text: true }),
          h("button", { type: "button", class: "btn secondary sm", onclick: () => downloadText(`${record.engine || "search"}-${record.id || "result"}.json`, text, "application/json") }, icon("download"), h("span", { text: "Download" })))),
      pre, showMore);
  }

  /** Parse without executing, drop active content and lock the document down with its own CSP before it goes into srcdoc. */
  function cleanHtml(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    doc.querySelectorAll("script, iframe, frame, object, embed, applet, base, form, meta[http-equiv], link[rel~='import'], link[rel~='preload'], noscript").forEach((node) => node.remove());
    doc.querySelectorAll("*").forEach((node) => { for (const attr of [...node.attributes]) if (/^on/i.test(attr.name) || (/^(href|src|action|formaction)$/i.test(attr.name) && /^\s*javascript:/i.test(attr.value))) node.removeAttribute(attr.name); });
    const meta = doc.createElement("meta");
    meta.setAttribute("http-equiv", "Content-Security-Policy");
    meta.setAttribute("content", "default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; font-src data:");
    doc.head.prepend(meta);
    return `<!doctype html>${doc.documentElement.outerHTML}`;
  }

  function renderHtml(record) {
    if (record.output === "md") return renderMarkdownPage(record);
    const holder = h("div", { class: "sk-htmlmode" });
    const draw = (html) => {
      let source = false;
      const frame = h("iframe", { class: "sk-frame", sandbox: "", srcdoc: cleanHtml(html), referrerpolicy: "no-referrer", title: "Stored page the engine saw", loading: "lazy" });
      const pre = h("pre", { class: "sk-raw", tabindex: "0", hidden: true, "aria-label": "HTML source" });
      pre.textContent = html.length > 600_000 ? `${html.slice(0, 600_000)}\n… truncated for display` : html;
      const toggle = h("button", { type: "button", class: "btn secondary sm", text: "View source", onclick: () => { source = !source; frame.hidden = source; pre.hidden = !source; toggle.textContent = source ? "View page" : "View source"; } });
      holder.replaceChildren(
        h("div", { class: "sk-tree-bar" }, h("span", { class: "sk-tree-status", text: `${Aira.fmt.bytes(html.length)} of HTML · scripts are disabled and the page cannot reach the network` }),
          h("div", { class: "btn-row" }, toggle, copyButton(html, { label: "Copy HTML", message: "HTML copied", text: true }),
            h("button", { type: "button", class: "btn secondary sm", onclick: () => downloadText(`${record.engine || "search"}-${record.id || "page"}.html`, html, "text/html") }, icon("download"), h("span", { text: "Download" })))),
        frame, pre);
    };
    if (typeof record.html === "string" && record.html) { draw(record.html); return holder; }
    if (record.html_available && record.id) {
      const button = h("button", { type: "button", class: "btn", text: "Load stored HTML" });
      button.addEventListener("click", () => Aira.withBusy(button, async () => {
        const data = await Aira.api(`/api/airaapi/searches/${encodeURIComponent(record.id)}?html=1`);
        if (typeof data.html !== "string" || !data.html) throw new Error("The stored HTML is no longer available.");
        record.html = data.html;
        draw(data.html);
      }));
      holder.append(h("div", { class: "empty" }, h("strong", { text: "HTML was stored for this search" }), h("div", { text: "It loads on request because pages can be large." }), button));
      return holder;
    }
    holder.append(emptyState("No HTML stored", "Turn on Raw HTML in the request options and run the search again to keep the page the engine saw."));
    return holder;
  }
  const emptyState = (title, text) => Aira.ui.empty(title, text);

  /** output=md: the engine's Markdown rendering, shown formatted with a source toggle (same shape as the HTML mode). */
  function renderMarkdownPage(record) {
    const holder = h("div", { class: "sk-htmlmode" });
    const draw = (text) => {
      let source = false;
      const formatted = h("div", { class: "sk-mdpage" }, markdownNode(text));
      const pre = h("pre", { class: "sk-raw", tabindex: "0", hidden: true, "aria-label": "Markdown source" });
      pre.textContent = text.length > 600_000 ? `${text.slice(0, 600_000)}\n… truncated for display` : text;
      const toggle = h("button", { type: "button", class: "btn secondary sm", text: "View source", onclick: () => { source = !source; formatted.hidden = source; pre.hidden = !source; toggle.textContent = source ? "View formatted" : "View source"; } });
      holder.replaceChildren(
        h("div", { class: "sk-tree-bar" }, h("span", { class: "sk-tree-status", text: `${Aira.fmt.bytes(text.length)} of Markdown returned by the engine` }),
          h("div", { class: "btn-row" }, toggle, copyButton(text, { label: "Copy Markdown", message: "Markdown copied", text: true }),
            h("button", { type: "button", class: "btn secondary sm", onclick: () => downloadText(`${record.engine || "search"}-${record.id || "page"}.md`, text, "text/markdown") }, icon("download"), h("span", { text: "Download" })))),
        formatted, pre);
    };
    if (typeof record.html === "string" && record.html) { draw(record.html); return holder; }
    if (record.html_available && record.id) {
      const button = h("button", { type: "button", class: "btn", text: "Load stored Markdown" });
      button.addEventListener("click", () => Aira.withBusy(button, async () => {
        const data = await Aira.api(`/api/airaapi/searches/${encodeURIComponent(record.id)}?html=1`);
        if (typeof data.html !== "string" || !data.html) throw new Error("The stored Markdown is no longer available.");
        record.html = data.html;
        draw(data.html);
      }));
      holder.append(h("div", { class: "empty" }, h("strong", { text: "Markdown was stored for this search" }), h("div", { text: "It loads on request because pages can be large." }), button));
      return holder;
    }
    holder.append(emptyState("No Markdown stored", "Choose Markdown in the request options and run the search again."));
    return holder;
  }

  function renderMetadata(record) {
    const result = isObject(record.result) ? record.result : {};
    const summary = [
      ["Search ID", record.id], ["Engine search ID", record.serpapi_id], ["Engine", record.engine], ["Status", record.status], ["Source", record.source],
      ["Label", record.label], ["Started", record.created_at ? Aira.fmt.datetime(record.created_at) : ""], ["Duration", record.duration_ms !== undefined && record.duration_ms !== null ? `${record.duration_ms} ms` : ""],
      ["HTTP status", record.http_status], ["Credits used", record.credits], ["Served from cache", record.cache_hit ? "Yes" : "No"], ["Results", record.result_count],
      ["Stored size", record.result_bytes ? Aira.fmt.bytes(record.result_bytes) : ""], ["Pinned", record.pinned ? "Yes" : ""], ["Error", record.error],
    ].filter(([, v]) => !isEmpty(v));
    const plan = planSections(result);
    const keyRows = Object.keys(result).map((key) => {
      const entry = plan.find((p) => p.key === key);
      const value = result[key];
      return { key, kind: Array.isArray(value) ? `list of ${value.length}` : isObject(value) ? `object, ${Object.keys(value).length} keys` : typeof value, view: entry ? (entry.renderer === "tree" ? "JSON tree" : titleForKey(key)) : "Header" };
    });
    const blocks = [
      section("This search", { body: h("dl", { class: "sk-kv" }, summary.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: redact(String(v)) })])) }),
      record.params && Object.keys(record.params).length ? section("Parameters sent", { count: Object.keys(record.params).length, body: kvList(record.params, { max: 60, rawKeys: true }) }) : null,
      isObject(result.search_metadata) ? section("search_metadata", { body: kvList(result.search_metadata, { rawKeys: true }) || note("Empty.") }) : null,
      isObject(result.search_parameters) ? section("search_parameters", { open: false, body: kvList(result.search_parameters, { max: 60, rawKeys: true }) || note("Empty.") }) : null,
      isObject(result.search_information) ? section("search_information", { open: false, body: kvList(result.search_information) || note("Empty.") }) : null,
      isObject(result.serpapi_pagination) || isObject(result.pagination) ? section("Pagination", { open: false, body: kvList(result.serpapi_pagination || result.pagination) || note("Empty.") }) : null,
      keyRows.length ? section("Top-level fields", { count: keyRows.length, open: false, body: h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Field" }), h("th", { text: "Shape" }), h("th", { text: "Shown as" }))),
        h("tbody", {}, keyRows.map((row) => h("tr", {}, h("td", {}, h("code", { text: row.key })), h("td", { text: row.kind }), h("td", { text: row.view })))))) }) : null,
    ].filter(Boolean);
    return h("div", { class: "sk-metadata" }, blocks);
  }

  /**
   * Draw a stored search record in one of the modes. mode: visual | json | table | html | raw | metadata | snippets.
   * Records without a body (errors, pending, pruned) get an explanation in every mode except html.
   */
  function renderResult(record, { mode = "visual", snippets } = {}) {
    const r = record || {};
    if (mode === "html") return renderHtml(r);
    if (mode === "snippets") {
      const info = snippets || {};
      return snippetPanel(info.engine || r.engine, info.params || r.params || {}, { baseUrl: info.baseUrl });
    }
    if (mode === "metadata") return renderMetadata(r);
    if (r.status === "running" && !r.result) return note("This search is still running. Open it again from History in a moment.", "");
    if (!r.result) {
      if (r.status === "error" || r.error) return failurePanel(r);
      return note("No result body is stored for this search. It may have been pruned by the retention settings; run it again to get fresh data.", "");
    }
    switch (mode) {
      case "json": return jsonTree(r.result, { maxDepth: 2, search: true, label: r.engine_name || r.engine || "Response" });
      case "table": return renderTable(r);
      case "raw": return renderRaw(r);
      default: {
        const body = renderVisual(r);
        if (typeof r.result.error === "string") body.prepend(note(redact(r.result.error), "warn"));
        return body;
      }
    }
  }

  const MODES = [["visual", "Visual"], ["json", "JSON"], ["table", "Table"], ["html", "HTML"], ["raw", "Raw"], ["metadata", "Metadata"], ["snippets", "Snippets"]];

  /**
   * Tabbed viewer around renderResult: remembers the chosen mode, keeps each pane alive while you switch tabs, and can be
   * pointed at a new record with setRecord(). Returns {node, setMode(m), setRecord(r), get mode}.
   */
  function resultViewer(record, { mode, onMode, snippets, modes } = {}) {
    let current = record;
    let active = mode || Aira.recall("serp.mode", "visual");
    const allowed = MODES.filter(([id]) => !modes || modes.includes(id));
    if (!allowed.some(([id]) => id === active)) active = allowed[0][0];
    const panes = new Map();
    const bar = h("div", { class: "tabs sk-modes", role: "tablist", "aria-label": "Result view", dataset: { roving: "select" } });
    const body = h("div", { class: "sk-viewer-body", role: "tabpanel", tabindex: "-1" });
    const paint = () => {
      for (const button of bar.children) {
        const on = button.dataset.mode === active;
        button.setAttribute("aria-selected", String(on));
        button.tabIndex = on ? 0 : -1;
        // The page tab shows whichever rendering the engine returned.
        if (button.dataset.mode === "html") button.textContent = current && current.output === "md" ? "Markdown" : "HTML";
      }
      if (!panes.has(active)) panes.set(active, renderResult(current, { mode: active, snippets }));
      body.replaceChildren(panes.get(active));
    };
    const setMode = (next) => { active = next; Aira.remember("serp.mode", next); paint(); if (onMode) onMode(next); };
    for (const [id, label] of allowed) bar.append(h("button", { type: "button", role: "tab", dataset: { mode: id }, text: label, onclick: () => setMode(id) }));
    rovingKeys(bar, "button");
    paint();
    return {
      node: h("div", { class: "sk-viewer" }, bar, body),
      setMode,
      setRecord: (next) => { current = next; panes.clear(); paint(); },
      get mode() { return active; },
    };
  }

  Aira.serp = {
    // contract (spec 7.1)
    paramForm, optionsForm, jsonTree, renderResult, codeSnippets, engineBadge, creditChip, statusBadge, locationPicker, confirmCost,
    // helpers other views can reuse
    resultViewer, snippetPanel, codeBlock, lineChart, markdownNode, section, stars, segmented, toggleSwitch, copyText, copyButton, downloadText,
    kvList, thumb, chip, humanize, cleanHtml, logic,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = logic;
})();

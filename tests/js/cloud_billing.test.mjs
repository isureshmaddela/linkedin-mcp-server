// Pure-logic tests for auto_apply_assistant/cloud-billing.js (window.AiraBillingLogic).
// No browser: the script is run in a vm with just enough stubs to register itself.
//
//   node tests/js/cloud_billing.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const source = readFileSync(join(root, "auto_apply_assistant", "cloud-billing.js"), "utf8");
const registered = [];
const window = { h: () => ({}) };
const Aira = { cloud: { register: (d) => registered.push(d), can: () => true }, fmt: { date: (s) => `D${s}` }, ui: {}, views: {} };
vm.runInNewContext(source, { window, Aira, document: { addEventListener() {} }, history: {}, location: {} });
const L = window.AiraBillingLogic;

let passed = 0;
function test(name, fn) {
  try { fn(); passed += 1; console.log(`ok - ${name}`); } catch (error) { console.error(`FAIL - ${name}\n${error.stack}`); process.exitCode = 1; }
}

const NOW = 1_000_000;
const DAY = 86400;

test("registers the billing and usage sections for the right roles", () => {
  const byId = Object.fromEntries(registered.map((d) => [d.id, d]));
  assert.deepEqual([...byId.billing.roles], ["owner", "billing"]);
  assert.deepEqual([...byId.usage.roles], ["admin", "auditor"]);
  assert.equal(byId.billing.key, "b");
});

test("every subscription state is explained with the dates that matter", () => {
  const trial = L.statusInfo({ status: "trialing", trial_end: NOW + 3 * DAY }, NOW);
  assert.match(trial.text, /3 days left/);
  assert.match(L.statusInfo({ status: "trialing", trial_end: NOW + DAY }, NOW).text, /1 day left/);
  const active = L.statusInfo({ status: "active", period_end: NOW + DAY }, NOW);
  assert.equal(active.level, "ok");
  assert.match(active.text, /Renews on D/);
  assert.match(L.statusInfo({ status: "active", period_end: 5, cancel_at_period_end: 1 }, NOW).text, /Cancels on D5/);
  const late = L.statusInfo({ status: "past_due", past_due_since: NOW }, NOW);
  assert.equal(late.level, "danger");
  assert.match(late.text, new RegExp(`read-only on D${NOW + 14 * DAY}`));
  assert.match(late.text, /Automation is stopped/);
  assert.match(L.statusInfo({ status: "read_only", deletion_scheduled_at: 77 }, NOW).text, /Deletion is scheduled for D77/);
  assert.match(L.statusInfo({ status: "cancelled", export_until: 99 }, NOW).text, /exported until D99/);
});

test("a scheduled deletion is mentioned even when the state is not read-only", () => {
  assert.match(L.statusInfo({ status: "cancelled", export_until: 1, deletion_scheduled_at: 55 }, NOW).text, /Scheduled for deletion on D55/);
});

test("plan changes are classified as upgrade, downgrade or none", () => {
  assert.equal(L.direction("starter", 10, "team", 25), "up");
  assert.equal(L.direction("team", 25, "starter", 10), "down");
  assert.equal(L.direction("starter", 10, "starter", 20), "up");
  assert.equal(L.direction("starter", 20, "starter", 8), "down");
  assert.equal(L.direction("starter", 10, "starter", 10), "same");
  assert.equal(L.direction("trial", 3, "starter", 5), "up");
});

test("the downgrade preview names what the organization would exceed", () => {
  const billing = { seat_usage: { candidates: 7, staff: 4 } };
  const starter = { limits: { candidate_seats: 25, staff_seats: 3 } };
  const over = L.exceedPreview(billing, starter, 5);
  assert.equal(over.length, 2);
  assert.match(over[0], /candidate seats \(7 in use, 5 allowed\)/);
  assert.match(over[1], /coach and admin seats \(4 in use, 3 allowed\)/);
  assert.equal(L.exceedPreview({ seat_usage: { candidates: 4, staff: 2 } }, starter, 5).length, 0);
  // the plan's own seat ceiling applies even when more seats are asked for
  assert.equal(L.exceedPreview({ seat_usage: { candidates: 30, staff: 0 } }, starter, 100).length, 1);
});

test("money, storage and retention read the way the spec's tables do", () => {
  assert.match(L.money(1100, "usd"), /11\.00/);
  assert.equal(L.bytes(100 * 1024 ** 2), "100 MB");
  assert.equal(L.bytes(2 * 1024 ** 3), "2 GB");
  assert.equal(L.bytes(25 * 1024 ** 3), "25 GB");
  assert.equal(L.retention(30), "30 days");
  assert.equal(L.retention(365), "1 year");
  assert.equal(L.retention(1095), "3 years");
});

test("the comparison rows reproduce the spec's plan table", () => {
  const starter = { plan_id: "starter", limits: { candidate_seats: 25, staff_seats: 3, apps_per_candidate_day: 10, apps_per_org_month: 1500, search_plans_per_candidate: 5, cover_letters_per_month: 500, api_searches_per_month: 1000, api_v1_keys: 0, monitors_per_candidate: 10, storage_bytes: 2 * 1024 ** 3, retention_days: 365, audit_days: 365, coach_max_scope: "act", support: "email" }, features: ["sources.aira_jobs", "sources.company_boards", "sources.google_jobs", "automation.submit", "ai.cover_letters", "api.searches", "auth.mfa"] };
  const row = (label) => L.LIMIT_ROWS.find(([name]) => name === label)[1](starter);
  assert.equal(row("Candidate seats"), "up to 25");
  assert.equal(row("Applications per organization per month"), "1,500");
  assert.equal(row("Document storage and retention"), "2 GB, 1 year");
  assert.equal(row("Coach scopes"), "view, act");
  assert.equal(row("API (console and /v1)"), "1,000 searches, no /v1");
  assert.equal(row("Automatic submission"), "Yes");
  assert.equal(row("Sign-in"), "Password, MFA");
  assert.equal(row("Support"), "Email");
  assert.match(row("Job sources"), /Google Jobs/);
  assert.doesNotMatch(row("Job sources"), /LinkedIn/);
  const trial = { plan_id: "trial", limits: { ...starter.limits, retention_days: 30, storage_bytes: 100 * 1024 ** 2, coach_max_scope: "view", support: "community", api_searches_per_month: 0 }, features: ["sources.aira_jobs", "api.console_read"] };
  assert.equal(L.LIMIT_ROWS.find(([n]) => n === "Document storage and retention")[1](trial), "100 MB, 30 days after the trial ends");
  assert.equal(L.LIMIT_ROWS.find(([n]) => n === "Automatic submission")[1](trial), "Review mode only");
  assert.equal(L.LIMIT_ROWS.find(([n]) => n === "API (console and /v1)")[1](trial), "Read-only history");
  const enterprise = { plan_id: "enterprise", limits: starter.limits, features: ["sources.linkedin", "auth.sso", "auth.scim", "api.v1", "ai.cover_letters"] };
  assert.equal(L.LIMIT_ROWS.find(([n]) => n === "Candidate seats")[1](enterprise), "Custom");
  assert.match(L.LIMIT_ROWS.find(([n]) => n === "Sign-in")[1](enterprise), /SSO.*SCIM/);
});

test("price and overage cells", () => {
  assert.equal(L.priceCell({ contact_sales: true }), "Contact sales");
  assert.match(L.priceCell({ seat_cents_month: 1400, min_seats: 5, currency: "usd" }), /\$14\.00 per candidate seat per month, from 5 seats/);
  assert.match(L.overageCell({ plan_id: "team", currency: "usd", overage: { application_cents: 10, search_cents: 2 } }), /\$0\.10 per extra application; \$0\.02 per extra search/);
  assert.equal(L.overageCell({ plan_id: "starter", overage: {} }), "Stops at the limit");
});

console.log(`${passed} passed`);

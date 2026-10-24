// Pure-logic tests for the hosted build's sign-in and coach screens.
//
//   node tests/js/cloud_auth.test.mjs
//
// Both files export their logic when loaded in node (they skip every DOM path).
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dir = join(here, "..", "..", "auto_apply_assistant");
const require = createRequire(import.meta.url);
const auth = require(join(dir, "cloud-auth.js"));
const coach = require(join(dir, "cloud-coach.js"));

let passed = 0;
let failed = 0;
const test = (name, fn) => {
  try { fn(); passed += 1; } catch (error) { failed += 1; console.error(`FAIL ${name}\n  ${error.message}`); }
};

test("anonymous routes are recognised and carry their token", () => {
  const token = "A".repeat(43);
  assert.deepEqual(auth.parseAnonymous(`#/invite/${token}`), { screen: "invite", token });
  assert.deepEqual(auth.parseAnonymous(`#/verify/${token}`), { screen: "verify", token });
  assert.deepEqual(auth.parseAnonymous(`#/reset/${token}`), { screen: "reset", token });
  assert.equal(auth.parseAnonymous("#/forgot").screen, "forgot");
  assert.equal(auth.parseAnonymous("#/signup").screen, "signup");
  assert.equal(auth.parseAnonymous("#/login").screen, "login");
});

test("app routes and malformed tokens are not anonymous routes", () => {
  for (const hash of ["", "#/", "#/jobs", "#/org/dashboard", "#/invite/short", "#/invite/" + "a/b".repeat(10), "#/platform", "#/forgot/extra"]) {
    assert.equal(auth.parseAnonymous(hash), null, hash);
  }
});

test("password hint mirrors the server rules", () => {
  assert.equal(auth.strength("").level, 0);
  assert.equal(auth.strength("short").ok, false);
  assert.match(auth.strength("short").label, /5 of 12/);
  assert.equal(auth.strength("aaaaaaaaaaaaaaaa").ok, false, "too few distinct characters");
  assert.equal(auth.strength("alice-password-1", "alice@acme.test").ok, false, "contains the email name");
  assert.equal(auth.strength("alice-password-1", "al@acme.test").ok, true, "short names are not checked");
  assert.equal(auth.strength("correct horse battery staple").level, 3);
  assert.equal(auth.strength("Abcdefg12345").level, 2);
});

test("setup keys are shown in groups of four and codes are cleaned", () => {
  assert.equal(auth.groups("ABCDEFGHIJKLMNOP"), "ABCD EFGH IJKL MNOP");
  assert.equal(auth.groups("ABCDEF"), "ABCD EF");
  assert.equal(auth.cleanCode(" 123 456 "), "123456");
  assert.equal(auth.cleanCode("a1b2-c3d4-e5f6"), "a1b2-c3d4-e5f6");
});

test("coach scopes decide what a queue item allows", () => {
  const held = { status: "needs_review" };
  const ready = { status: "ready" };
  assert.equal(coach.queueAction({ ...held, scope: "view" }).kind, "view");
  assert.equal(coach.queueAction({ ...held, scope: "act" }).kind, "answer");
  assert.equal(coach.queueAction({ ...held, scope: "approve" }).kind, "answer");
  assert.equal(coach.queueAction({ ...ready, scope: "act" }).kind, "view", "act cannot submit");
  assert.equal(coach.queueAction({ ...ready, scope: "approve" }).kind, "approve");
  assert.equal(coach.queueAction({ ...ready }).kind, "view", "no scope means view only");
  assert.ok(coach.atLeast("approve", "act") && !coach.atLeast("view", "act"));
});

test("held time reads in hours and days", () => {
  const now = Date.parse("2026-10-24T12:00:00Z");
  assert.equal(Math.round(coach.heldHours("2026-10-23T12:00:00+00:00", now)), 24);
  assert.equal(coach.heldHours("", now), null);
  assert.equal(coach.heldLabel(0.2), "held for under an hour");
  assert.equal(coach.heldLabel(30), "held for 30 h");
  assert.equal(coach.heldLabel(72), "held for 3 days");
  assert.equal(coach.heldLabel(null), "");
});

test("all three required consents are described in plain language", () => {
  for (const kind of ["automation", "coach_access", "sensitive_policy"]) {
    assert.ok(coach.CONSENTS[kind].text.length > 80, kind);
  }
  assert.match(coach.CONSENTS.sensitive_policy.text, /never sent to the AI model/);
});

console.log(`${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

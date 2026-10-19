import test from "node:test";
import assert from "node:assert/strict";
import { canSuspendTab, hasFormData, shouldQuitOnClose } from "../../browser/privacy/Lifecycle.sys.mjs";

test("full exit requests are limited to an uncancelled final browser window", () => {
  const eligible = { completeExit: true, isLast: true };
  assert.equal(shouldQuitOnClose(eligible), true);
  for (const changed of [{ completeExit: false }, { isLast: false }, { quitting: true }, { defaultPrevented: true }]) {
    assert.equal(shouldQuitOnClose({ ...eligible, ...changed }), false);
  }
});

const idle = { connected: true, remote: true, webPage: true, selected: false, pinned: false,
  pending: false, busy: false, audio: false, sharing: false, formData: false,
  downloadsActive: false, lastAccessed: 1000 };
const clock = { now: 100_000, idleMs: 60_000 };

test("automatic unloading preserves selected, active, pinned, media, form and download tabs", () => {
  assert.equal(canSuspendTab(idle, clock), true);
  for (const key of ["selected", "pinned", "pending", "busy", "audio", "sharing", "formData", "downloadsActive"]) {
    assert.equal(canSuspendTab({ ...idle, [key]: true }, clock), false, key);
  }
  for (const key of ["connected", "remote", "webPage"]) {
    assert.equal(canSuspendTab({ ...idle, [key]: false }, clock), false, key);
  }
});

test("missing metadata, new activity and a disabled timeout prevent unloading", () => {
  assert.equal(canSuspendTab(null, clock), false);
  assert.equal(canSuspendTab({ ...idle, lastAccessed: undefined }, clock), false);
  assert.equal(canSuspendTab({ ...idle, lastAccessed: 90_000 }, clock), false);
  assert.equal(canSuspendTab(idle, { ...clock, idleMs: 0 }), false);
  assert.equal(canSuspendTab(idle, { ...clock, now: NaN }), false);
});

test("form preservation detects nested frame and history entries without exposing field data", () => {
  assert.equal(hasFormData({ entries: [{ children: [{ formdata: { id: { input: "sensitive" } } }] }] }), true);
  assert.equal(hasFormData({ formdata: { xpath: { "/html/input": "value" } } }), true);
  assert.equal(hasFormData({ entries: [{ url: "https://example.test" }], formdata: {} }), false);
  assert.equal(hasFormData(null), false);
});

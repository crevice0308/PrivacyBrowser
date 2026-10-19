/* SPDX-License-Identifier: MPL-2.0 */
import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_HISTORY_FLAGS, HISTORY_KEYS, PrivacyController, buildPreferencePlan, categoriesToClear, normalizeHistoryFlags } from "../../browser/privacy/PrivacyCore.sys.mjs";

class Adapter {
  constructor() { this.calls = []; this.searches = []; this.fail = new Set(); this.dirty = false; this.bookmarks = ["https://example.test/bookmark"]; }
  async applyPreferences(plan) { this.plan = plan; this.calls.push("prefs"); }
  async clearCategory(category) { this.calls.push(`clear:${category}`); if (this.fail.has(category)) throw new Error("sensitive path must never be propagated"); if (category === "search") this.searches = []; }
  async setDirty(value) { this.dirty = value; this.calls.push(`dirty:${value}`); }
  async saveFlags(flags) { this.saved = flags; this.calls.push("save"); }
  async readSearchHistory() { return [...this.searches]; }
  async writeSearchHistory(history) { this.searches = history; this.calls.push("search-write"); }
}

test("all eight categories default to non-persistence", () => {
  assert.equal(HISTORY_KEYS.length, 8);
  assert.deepEqual(Object.values(DEFAULT_HISTORY_FLAGS), Array(8).fill(false));
  assert.ok(Object.isFrozen(DEFAULT_HISTORY_FLAGS));
  assert.deepEqual(categoriesToClear(), HISTORY_KEYS);
});

test("all 256 combinations retain independent flag settings", () => {
  for (let mask = 0; mask < 256; mask++) {
    const flags = Object.fromEntries(HISTORY_KEYS.map((key, i) => [key, Boolean(mask & (1 << i))]));
    const prefs = buildPreferencePlan(flags);
    for (const key of HISTORY_KEYS) assert.equal(prefs[`privacybrowser.history.${key}`], flags[key]);
    assert.deepEqual(categoriesToClear(flags), HISTORY_KEYS.filter(key => !flags[key]));
    assert.equal(prefs["places.history.enabled"], flags.browsing);
    assert.equal(prefs["browser.formfill.enable"], flags.forms);
    assert.equal(prefs["browser.cache.disk.enable"], flags.cache);
    assert.equal(prefs["browser.sessionstore.resume_from_crash"], flags.sessions);
  }
});

test("plan preserves sandbox isolation and avoids blanket PB mode", () => {
  const prefs = buildPreferencePlan();
  assert.equal(Object.hasOwn(prefs, "browser.privatebrowsing.autostart"), false);
  assert.equal(Object.keys(prefs).some(key => /sandbox|fission|safebrowsing/.test(key)), false);
  assert.equal(prefs["browser.sessionstore.max_tabs_undo"], 0);
  assert.equal(prefs["browser.cache.memory.enable"], true);
});

test("flag validation rejects typo, coercion, null, and arrays", () => {
  for (const value of [{ browsing: "true" }, { history: true }, { forms: 1 }, null, []]) {
    assert.throws(() => normalizeHistoryFlags(value), TypeError);
  }
  assert.deepEqual(normalizeHistoryFlags({ browsing: true }).search, false);
});

test("startup marks unclean before cleanup and waits for all categories", async () => {
  const adapter = new Adapter();
  const controller = new PrivacyController(adapter);
  assert.equal((await controller.startup()).state, "ready");
  assert.deepEqual(adapter.calls.slice(0, 2), ["dirty:true", "prefs"]);
  assert.deepEqual(adapter.calls.slice(2), HISTORY_KEYS.map(key => `clear:${key}`));
  assert.equal(adapter.dirty, true);
});

test("startup cleans only disabled categories and preserves enabled data", async () => {
  const adapter = new Adapter();
  adapter.searches = ["あいうえお"];
  const controller = new PrivacyController(adapter, { search: true, cookies: true, forms: true });
  await controller.startup();
  assert.deepEqual(adapter.searches, ["あいうえお"]);
  assert.equal(adapter.calls.includes("clear:cookies"), false);
  assert.equal(adapter.calls.includes("clear:forms"), false);
});

test("failed startup remains blocked, attempts other categories, and keeps dirty marker", async () => {
  const adapter = new Adapter();
  adapter.fail.add("cookies");
  const controller = new PrivacyController(adapter);
  await assert.rejects(controller.startup(), error => {
    assert.deepEqual(error.categories, ["cookies"]);
    assert.doesNotMatch(error.message, /sensitive/);
    return true;
  });
  assert.equal(controller.state, "failed");
  assert.equal(adapter.dirty, true);
  assert.ok(adapter.calls.includes("clear:siteStorage"));
  await assert.rejects(controller.configure({ browsing: true }), /not ready/);
  await assert.rejects(controller.recordSearch("private"), /not ready/);
});

test("turning off persistence saves strict policy before deleting data", async () => {
  const adapter = new Adapter();
  const controller = new PrivacyController(adapter, { cookies: true, sessions: true });
  await controller.startup();
  adapter.calls = [];
  const result = await controller.configure({ cookies: false });
  assert.deepEqual(adapter.calls, ["save", "prefs", "clear:cookies"]);
  assert.equal(adapter.saved.cookies, false);
  assert.equal(result.restartRequired, true);
  assert.equal(controller.flags.sessions, true);
});

test("invalid configuration is atomic and does not poison ready controller", async () => {
  const adapter = new Adapter();
  const controller = new PrivacyController(adapter);
  await controller.startup();
  const count = adapter.calls.length;
  await assert.rejects(controller.configure({ forms: "false" }), TypeError);
  assert.equal(controller.state, "ready");
  assert.equal(controller.flags.forms, false);
  assert.equal(adapter.calls.length, count);
});

test("failure after disabling preserves stricter preference for next startup", async () => {
  const adapter = new Adapter();
  const controller = new PrivacyController(adapter, { cookies: true });
  await controller.startup();
  adapter.fail.add("cookies");
  await assert.rejects(controller.configure({ cookies: false }), /cleanup failed/);
  assert.equal(adapter.saved.cookies, false);
  assert.equal(controller.flags.cookies, false);
  assert.equal(controller.state, "failed");
});

test("queued settings updates do not overwrite each other", async () => {
  const adapter = new Adapter();
  const controller = new PrivacyController(adapter);
  await controller.startup();
  await Promise.all([controller.configure({ browsing: true }), controller.configure({ forms: true }), controller.configure({ cache: true })]);
  assert.equal(controller.flags.browsing, true);
  assert.equal(controller.flags.forms, true);
  assert.equal(controller.flags.cache, true);
});

test("search saving is opt-in, bounded, Unicode-safe, and separate from forms", async () => {
  const adapter = new Adapter();
  const controller = new PrivacyController(adapter);
  await controller.startup();
  assert.equal(await controller.recordSearch("日本語テスト"), false);
  assert.equal(adapter.calls.includes("search-write"), false);
  await controller.configure({ search: true });
  assert.equal(controller.flags.forms, false);
  await controller.recordSearch("あいうえお");
  await controller.recordSearch("日本語テスト");
  await controller.recordSearch("あいうえお");
  assert.deepEqual(await controller.readSearchHistory(), ["あいうえお", "日本語テスト"]);
  assert.equal(await controller.recordSearch(" "), false);
  assert.equal(await controller.recordSearch("a".repeat(4097)), false);
  adapter.searches = Array.from({ length: 1100 }, (_, i) => `q${i}`);
  await controller.recordSearch("last");
  assert.equal(adapter.searches.length, 1000);
});

test("clear all keeps bookmarks and preserves policy flags", async () => {
  const adapter = new Adapter();
  const controller = new PrivacyController(adapter, Object.fromEntries(HISTORY_KEYS.map(key => [key, true])));
  await controller.startup();
  adapter.calls = [];
  await controller.clearAll();
  assert.deepEqual(adapter.calls, HISTORY_KEYS.map(key => `clear:${key}`));
  assert.deepEqual(adapter.bookmarks, ["https://example.test/bookmark"]);
  assert.ok(Object.values(controller.flags).every(Boolean));
});

test("shutdown clears disabled categories, marks clean last, and is idempotent", async () => {
  const adapter = new Adapter();
  const controller = new PrivacyController(adapter);
  await controller.startup();
  adapter.calls = [];
  assert.equal((await controller.shutdown()).state, "closed");
  assert.equal(adapter.calls.at(-1), "dirty:false");
  assert.equal(adapter.dirty, false);
  const count = adapter.calls.length;
  await controller.shutdown();
  assert.equal(adapter.calls.length, count);
});

test("failed shutdown leaves dirty marker and can retry after failure is fixed", async () => {
  const adapter = new Adapter();
  const controller = new PrivacyController(adapter);
  await controller.startup();
  adapter.fail.add("cache");
  await assert.rejects(controller.shutdown(), /cache/);
  assert.equal(adapter.dirty, true);
  adapter.fail.clear();
  await controller.shutdown();
  assert.equal(adapter.dirty, false);
});

test("uninitialized adapter and repeated startup are rejected", async () => {
  assert.throws(() => new PrivacyController({}), /Missing/);
  const controller = new PrivacyController(new Adapter());
  await assert.rejects(controller.shutdown(), /never started/);
  await controller.startup();
  await assert.rejects(controller.startup(), /already attempted/);
});

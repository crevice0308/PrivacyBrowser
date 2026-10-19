/* SPDX-License-Identifier: MPL-2.0 */
// Exercise Gecko adapter I/O against real temporary files. These mocks test the
// adapter boundary; they do not replace Firefox runtime or profile validation.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

const root = await fs.mkdtemp(path.join(os.tmpdir(), "privacybrowser-adapter-"));
const profile = path.join(root, "profile");
await fs.mkdir(profile);
const prefs = new Map();
const cleared = [];
let clearedPlacesHistory = 0;
const formChanges = [];
const prefObservers = [];
function setPref(key, value) {
  const previous = prefs.get(key);
  prefs.set(key, value);
  if (value !== previous) for (const { prefix, observer } of prefObservers) if (key.startsWith(prefix)) observer.observe(null, "nsPref:changed", key);
}
globalThis.Ci = { nsIFile: {}, nsIClearDataService: { CLEAR_HISTORY: 1, CLEAR_COOKIES: 2, CLEAR_DOWNLOADS: 4, CLEAR_NETWORK_CACHE: 8, CLEAR_IMAGE_CACHE: 16, CLEAR_JS_CACHE: 32, CLEAR_CSS_CACHE: 64, CLEAR_DOM_STORAGES: 128, CLEAR_EME: 256 } };
globalThis.Services = {
  dirsvc: { get() { return { path: profile }; } },
  prefs: {
    getBoolPref: (key, fallback) => prefs.get(key) ?? fallback,
    getIntPref: (key, fallback) => prefs.get(key) ?? fallback,
    getStringPref: (key, fallback) => prefs.get(key) ?? fallback,
    setBoolPref: setPref, setIntPref: setPref, setStringPref: setPref,
    savePrefFile() {},
    addObserver(prefix, observer) { prefObservers.push({ prefix, observer }); },
    removeObserver(prefix, observer) { const i = prefObservers.findIndex(entry => entry.prefix === prefix && entry.observer === observer); if (i !== -1) prefObservers.splice(i, 1); },
  },
  clearData: { deleteData(mask, callback) { cleared.push(mask); callback.onDataDeleted(0); } },
  obs: { notifyObservers() {} },
};
globalThis.ChromeUtils = { defineESModuleGetters(target) {
  target.FormHistory = { async update(change) { formChanges.push(change); } };
  target.PlacesUtils = { history: { async clear() { clearedPlacesHistory++; } } };
} };
globalThis.PathUtils = { join: path.join, isAbsolute: path.isAbsolute };
const notFound = error => { if (error.code === "ENOENT") error.name = "NotFoundError"; throw error; };
globalThis.IOUtils = {
  remove: async (target, options = {}) => fs.rm(target, { force: options.ignoreAbsent === true, recursive: options.recursive === true }).catch(notFound),
  readJSON: async target => JSON.parse(await fs.readFile(target, "utf8").catch(notFound)),
  writeJSON: async (target, value, options) => { await fs.writeFile(options.tmpPath, JSON.stringify(value)); await fs.rename(options.tmpPath, target); },
  setPermissions: fs.chmod,
  makeDirectory: async (target, options) => fs.mkdir(target, { recursive: options.ignoreExisting, mode: options.permissions }),
  stat: async target => fs.stat(target).catch(notFound),
  writeUTF8: (target, contents, options) => fs.writeFile(target, contents, { flag: options.mode === "append" ? "a" : "w" }),
  readUTF8: async target => fs.readFile(target, "utf8").catch(notFound),
  move: fs.rename,
  exists: async target => fs.access(target).then(() => true, () => false),
};

const { GeckoPrivacyAdapter, GeckoDiagnosticsSink, PrivacyBrowserPrivacy, createGeckoDiagnostics } = await import("../../browser/privacy/GeckoPrivacy.sys.mjs");
const { DiagnosticsLogger } = await import("../../browser/privacy/Diagnostics.sys.mjs");

test.after(async () => { await fs.rm(root, { recursive: true, force: true }); });

test("adapter clears Places history independently without native session-purge side effects", async () => {
  const adapter = new GeckoPrivacyAdapter();
  cleared.length = 0;
  const previousClears = clearedPlacesHistory;
  for (const category of ["browsing", "cookies", "downloads", "cache", "siteStorage"]) await adapter.clearCategory(category);
  assert.equal(clearedPlacesHistory, previousClears + 1);
  assert.deepEqual(cleared, [2, 4, 8 | 16 | 32 | 64, 128 | 256]);
  assert.equal(cleared.includes(Ci.nsIClearDataService.CLEAR_HISTORY), false);
  await assert.rejects(adapter.clearCategory("bookmarks"), /Unsupported/);
});

test("session cleanup removes only known session files, preserving bookmark and download bytes", async () => {
  const adapter = new GeckoPrivacyAdapter();
  for (const name of ["sessionstore.jsonlz4", "sessionstore.js", "sessionstore.bak", "sessionstore.baklz4", "places.sqlite", "downloaded-file.bin"]) await fs.writeFile(path.join(profile, name), name);
  await fs.mkdir(path.join(profile, "sessionstore-backups"));
  await fs.writeFile(path.join(profile, "sessionstore-backups", "recovery.jsonlz4"), "private-session");
  await adapter.clearCategory("sessions");
  assert.equal(await IOUtils.exists(path.join(profile, "sessionstore-backups")), false);
  assert.equal(await IOUtils.exists(path.join(profile, "sessionstore.jsonlz4")), false);
  assert.equal(await fs.readFile(path.join(profile, "places.sqlite"), "utf8"), "places.sqlite");
  assert.equal(await fs.readFile(path.join(profile, "downloaded-file.bin"), "utf8"), "downloaded-file.bin");
});

test("separate search history survives form cleanup and uses private permissions", async () => {
  const adapter = new GeckoPrivacyAdapter();
  await adapter.writeSearchHistory(["あいうえお", "日本語テスト"]);
  const target = path.join(profile, "privacybrowser-search-history.json");
  assert.equal((await fs.stat(target)).mode & 0o777, 0o600);
  await adapter.clearCategory("forms");
  assert.deepEqual(await adapter.readSearchHistory(), ["あいうえお", "日本語テスト"]);
  assert.deepEqual(formChanges.at(-1), { op: "remove" });
  await adapter.clearCategory("search");
  assert.deepEqual(await adapter.readSearchHistory(), []);
  assert.deepEqual(formChanges.at(-1), { op: "remove", fieldname: "searchbar-history" });
});

test("filesystem diagnostics rotation retains bounded files and preserves neighboring files", async () => {
  const directory = path.join(root, "logs");
  const sink = new GeckoDiagnosticsSink(directory);
  const logger = new DiagnosticsLogger({ sink, enabled: true, level: "debug", maxBytes: 1024, maxFiles: 3 });
  await fs.mkdir(directory);
  await fs.writeFile(path.join(directory, "do-not-delete.txt"), "retained");
  for (let i = 0; i < 100; i++) await logger.log("resource-sample", { pid: i, residentBytes: 1024 * 1024 }, "debug");
  const names = await fs.readdir(directory);
  assert.deepEqual(names.sort(), ["diagnostics.1.jsonl", "diagnostics.2.jsonl", "diagnostics.jsonl", "do-not-delete.txt"].sort());
  for (const name of names.filter(name => name.endsWith(".jsonl"))) {
    const stat = await fs.stat(path.join(directory, name));
    assert.ok(stat.size <= 1024);
    assert.equal(stat.mode & 0o777, 0o600);
  }
  await logger.deleteLogs();
  assert.deepEqual(await fs.readdir(directory), ["do-not-delete.txt"]);
});

test("Gecko singleton observes UI preference changes and records prior unclean state", async () => {
  prefs.set("privacybrowser.uncleanExit", true);
  await PrivacyBrowserPrivacy.startup();
  assert.equal(prefs.get("privacybrowser.previousUnclean"), true);
  Services.prefs.setBoolPref("privacybrowser.history.search", true);
  await new Promise(resolve => setImmediate(resolve));
  await PrivacyBrowserPrivacy.controller._queue;
  assert.equal(PrivacyBrowserPrivacy.flags.search, true);
  await PrivacyBrowserPrivacy.recordSearch("日本語テスト");
  assert.deepEqual(await PrivacyBrowserPrivacy.readSearchHistory(), ["日本語テスト"]);
  Services.prefs.setBoolPref("privacybrowser.history.search", false);
  await new Promise(resolve => setImmediate(resolve));
  await PrivacyBrowserPrivacy.controller._queue;
  assert.equal(await IOUtils.exists(path.join(profile, "privacybrowser-search-history.json")), false);
  await PrivacyBrowserPrivacy.shutdown();
  assert.equal(prefs.get("privacybrowser.uncleanExit"), false);
});

test("Gecko logger observes opt-in and directory changes; disabled startup creates no log directory", async () => {
  const beforeDirectory = path.join(root, "off-logs");
  const afterDirectory = path.join(root, "enabled-logs");
  prefs.set("privacybrowser.diagnostics.directory", beforeDirectory);
  const logger = createGeckoDiagnostics();
  await logger.log("crash", {}, "error");
  assert.equal(await IOUtils.exists(beforeDirectory), false);
  Services.prefs.setBoolPref("privacybrowser.diagnostics.enabled", true);
  await new Promise(resolve => setImmediate(resolve));
  await logger.log("crash", { code: "unknown" }, "error");
  assert.equal(await IOUtils.exists(path.join(beforeDirectory, "diagnostics.jsonl")), true);
  Services.prefs.setStringPref("privacybrowser.diagnostics.directory", afterDirectory);
  await new Promise(resolve => setImmediate(resolve));
  await logger.log("crash", { code: "api" }, "error");
  assert.equal(await IOUtils.exists(path.join(afterDirectory, "diagnostics.jsonl")), true);
  await logger.dispose();
  assert.equal(prefObservers.length, 0);
});

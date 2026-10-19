/* SPDX-License-Identifier: MPL-2.0 */
import test from "node:test";
import assert from "node:assert/strict";
import { DiagnosticsLogger, sanitizeDiagnosticData, sanitizeDiagnosticRecord } from "../../browser/privacy/Diagnostics.sys.mjs";

class MemorySink {
  constructor() { this.active = ""; this.archives = []; this.calls = []; }
  async size() { this.calls.push("size"); return Buffer.byteLength(this.active); }
  async append(text) { this.calls.push("append"); this.active += text; }
  async rotate(maxFiles) { this.calls.push("rotate"); this.archives = [this.active, ...this.archives].slice(0, maxFiles - 1); this.active = ""; }
  async removeAll() { this.calls.push("delete"); this.active = ""; this.archives = []; }
  async readArchives() { this.calls.push("read"); return [...this.archives.toReversed(), this.active]; }
  async exportZip(destination, files) { this.calls.push("export"); this.package = { destination, files }; return destination; }
}
const clock = () => new Date("2026-10-19T00:00:00.000Z");
const loggerWith = options => { const sink = new MemorySink(); return { sink, logger: new DiagnosticsLogger({ sink, clock, ...options }) }; };

test("diagnostics disabled by default creates no files or I/O", async () => {
  const { sink, logger } = loggerWith();
  assert.equal(await logger.log("startup", {}, "error"), false);
  await logger.flush();
  assert.deepEqual(sink.calls, []);
});

test("JSONL contains only fixed schema and permitted primitive data", async () => {
  const { sink, logger } = loggerWith({ enabled: true, level: "debug" });
  await logger.log("resource-sample", { pid: 55, residentBytes: 1024, cpuPercent: 3.5, cleanExit: true, code: "unknown", url: "https://private.test", query: "secret", password: "secret", cookie: "secret", stack: "secret", error: new Error("secret") }, "debug");
  const record = JSON.parse(sink.active);
  assert.equal(record.timestamp, clock().toISOString());
  assert.deepEqual(record.data, { pid: 55, residentBytes: 1024, cpuPercent: 3.5, cleanExit: true, code: "unknown" });
  assert.doesNotMatch(sink.active, /private|secret|password|cookie|stack|query|url/);
  assert.equal(sink.active.split("\n").length, 2);
});

test("untrusted accessors are not evaluated and nested objects are excluded", () => {
  const data = { residentBytes: { secret: "value" }, pid: Infinity, cpuPercent: NaN, tabId: -1, code: "https://private.test" };
  Object.defineProperty(data, "durationMs", { get() { throw new Error("must not access"); } });
  assert.deepEqual({ ...sanitizeDiagnosticData(data) }, {});
});

test("prototype-inherited fields cannot enter records", () => {
  const data = Object.create({ pid: 44, code: "io" });
  assert.deepEqual({ ...sanitizeDiagnosticData(data) }, {});
});

test("unknown events, arbitrary message levels, and low severity are rejected", async () => {
  const { sink, logger } = loggerWith({ enabled: true });
  assert.equal(await logger.log("https://private.test", {}, "error"), false);
  assert.equal(await logger.log("startup", {}, "secret"), false);
  assert.equal(await logger.log("startup", {}, "debug"), false);
  assert.equal(await logger.log("startup", {}, "info"), false);
  assert.equal(await logger.log("internal-exception", { code: "api" }, "error"), true);
  assert.equal(sink.active.trim().split("\n").length, 1);
});

test("non-finite, negative, and oversized numbers never serialize", () => {
  const data = sanitizeDiagnosticData({ pid: -1, residentBytes: Infinity, cpuPercent: NaN, durationMs: Number.MAX_VALUE, tabCount: 3 });
  assert.deepEqual({ ...data }, { tabCount: 3 });
});

test("rotation enforces active and total byte/file limits under concurrent calls", async () => {
  const { sink, logger } = loggerWith({ enabled: true, level: "debug", maxBytes: 1024, maxFiles: 3 });
  await Promise.all(Array.from({ length: 100 }, (_, i) => logger.log("resource-sample", { pid: i, residentBytes: 1000 }, "debug")));
  assert.equal(sink.archives.length, 2);
  assert.ok([sink.active, ...sink.archives].every(text => Buffer.byteLength(text) <= 1024));
  assert.ok(Buffer.byteLength(sink.active + sink.archives.join("")) <= 3072);
  assert.ok(sink.calls.includes("rotate"));
  for (const line of [sink.active, ...sink.archives].flatMap(text => text.trim().split("\n"))) assert.equal(JSON.parse(line).event, "resource-sample");
});

test("single-file logging rotates without preserving archives", async () => {
  const { sink, logger } = loggerWith({ enabled: true, level: "debug", maxBytes: 1024, maxFiles: 1 });
  for (let i = 0; i < 30; i++) await logger.log("startup", { pid: i }, "debug");
  assert.equal(sink.archives.length, 0);
  assert.ok(Buffer.byteLength(sink.active) <= 1024);
});

test("error flood has a bounded write queue and exposes dropped record count", async () => {
  const { logger } = loggerWith({ enabled: true });
  const results = await Promise.all(Array.from({ length: 1000 }, () => logger.log("internal-exception", { code: "api" }, "error")));
  assert.equal(results.filter(Boolean).length, 256);
  assert.equal(logger.droppedRecordCount, 744);
  assert.equal(logger._pendingRecords, 0);
  assert.equal(await logger.log("crash", {}, "error"), true);
});

test("disabling cancels queued writes and subsequent logging", async () => {
  const { sink, logger } = loggerWith({ enabled: true });
  const queued = logger.log("internal-exception", {}, "error");
  logger.configure({ enabled: false });
  assert.equal(await queued, false);
  assert.deepEqual(sink.calls, []);
  assert.equal(await logger.log("crash", {}, "error"), false);
});

test("log deletion cancels queued writes and clears all retained logs", async () => {
  const { sink, logger } = loggerWith({ enabled: true });
  await logger.log("crash", {}, "error");
  const queued = logger.log("crash", {}, "error");
  await logger.deleteLogs();
  assert.equal(await queued, false);
  assert.equal(sink.active, "");
  assert.deepEqual(sink.archives, []);
});

test("export requires explicit user action even when logging is disabled", async () => {
  const { sink, logger } = loggerWith();
  await assert.rejects(logger.exportPackage("/tmp/test.zip"), /explicit user action/);
  assert.deepEqual(sink.calls, []);
  assert.equal(await logger.exportPackage("/tmp/test.zip", { userInitiated: true }), "/tmp/test.zip");
  assert.deepEqual(sink.package.files.map(file => file.name), ["manifest.json", "diagnostics.jsonl"]);
});

test("export re-sanitizes existing logs and excludes raw profile data", async () => {
  const { sink, logger } = loggerWith({ enabled: true });
  sink.active = [
    JSON.stringify({ timestamp: clock().toISOString(), event: "crash", level: "error", data: { pid: 2, url: "https://secret.test", password: "secret" }, unexpected: "secret" }),
    "{invalid}",
    JSON.stringify({ timestamp: clock().toISOString(), event: "secret", level: "error", data: { pid: 3 } }),
    JSON.stringify({ timestamp: "https://secret.test", event: "crash", level: "error", data: {} }),
  ].join("\n");
  await logger.exportPackage("/tmp/test.zip", { userInitiated: true });
  const output = JSON.stringify(sink.package.files);
  assert.doesNotMatch(output, /secret|password|url|unexpected/);
  assert.equal(JSON.parse(sink.package.files[0].contents).recordCount, 1);
  assert.deepEqual(JSON.parse(sink.package.files[1].contents).data, { pid: 2 });
});

test("I/O failure does not break browser and is reported as a count", async () => {
  const { sink, logger } = loggerWith({ enabled: true });
  sink.append = async () => { throw new Error("secret path"); };
  assert.equal(await logger.log("crash", {}, "error"), false);
  assert.equal(logger.errorCount, 1);
  await logger.flush();
  assert.equal(sink.active, "");
});

test("invalid configuration rejects atomically", () => {
  const { logger } = loggerWith();
  for (const options of [{ level: "secret" }, { maxBytes: 0 }, { maxBytes: 1024.5 }, { maxFiles: 0 }, { maxFiles: 21 }, { enabled: "true" }]) {
    assert.throws(() => logger.configure(options));
  }
  assert.equal(logger.enabled, false);
  assert.equal(logger.level, "warn");
});

test("corrupt records and invalid clock are rejected without log writes", async () => {
  assert.equal(sanitizeDiagnosticRecord(null), null);
  assert.equal(sanitizeDiagnosticRecord({ timestamp: "never", event: "crash", level: "error" }), null);
  const { sink, logger } = loggerWith({ enabled: true, clock: () => new Date("bad") });
  assert.equal(await logger.log("crash", {}, "error"), false);
  assert.deepEqual(sink.calls, []);
});

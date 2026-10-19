/* SPDX-License-Identifier: MPL-2.0 */
/** Diagnostic records accept no free-form strings, Error messages, or URLs. */
export const DIAGNOSTIC_EVENTS = Object.freeze([
  "startup", "shutdown", "crash", "resource-sample", "tab-terminated",
  "update-failure", "extension-error", "internal-exception", "privacy-cleanup-failure",
]);
const LEVELS = Object.freeze({ debug: 0, info: 1, warn: 2, error: 3 });
const NUMERIC_FIELDS = new Set([
  "pid", "tabId", "extensionIndex", "cpuPercent", "residentBytes", "privateBytes",
  "durationMs", "processCount", "tabCount", "failedCategoryCount", "exitCode", "errorCount",
]);
const BOOLEAN_FIELDS = new Set(["cleanExit", "recovered", "forced", "succeeded"]);
const CODES = new Set(["unknown", "network", "signature", "permission", "conflict", "unavailable", "io", "api", "cleanup"]);
const utf8Length = value => new TextEncoder().encode(value).length;
const ownValue = (value, key) => {
  const descriptor = Object.getOwnPropertyDescriptor(value, key);
  return descriptor && "value" in descriptor ? descriptor.value : undefined;
};

export function sanitizeDiagnosticData(value = {}) {
  const output = Object.create(null);
  if (!value || typeof value !== "object") return output;
  for (const key of NUMERIC_FIELDS) {
    const number = ownValue(value, key);
    if (typeof number === "number" && Number.isFinite(number) && number >= 0 && number <= Number.MAX_SAFE_INTEGER) output[key] = number;
  }
  for (const key of BOOLEAN_FIELDS) {
    const boolean = ownValue(value, key);
    if (typeof boolean === "boolean") output[key] = boolean;
  }
  const code = ownValue(value, "code");
  if (typeof code === "string" && CODES.has(code)) output.code = code;
  return output;
}

export function sanitizeDiagnosticRecord(record) {
  if (!record || typeof record !== "object") return null;
  const event = ownValue(record, "event");
  const level = ownValue(record, "level");
  const timestamp = ownValue(record, "timestamp");
  if (!DIAGNOSTIC_EVENTS.includes(event) || !Object.hasOwn(LEVELS, level)) return null;
  if (typeof timestamp !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(timestamp) || !Number.isFinite(Date.parse(timestamp))) return null;
  return { version: 1, timestamp, level, event, data: sanitizeDiagnosticData(ownValue(record, "data")) };
}

function validateOptions({ level, maxBytes, maxFiles }) {
  if (!Object.hasOwn(LEVELS, level)) throw new TypeError("Unsupported diagnostics level");
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1024 || maxBytes > 100 * 1024 * 1024) throw new RangeError("Log size must be between 1 KiB and 100 MiB");
  if (!Number.isSafeInteger(maxFiles) || maxFiles < 1 || maxFiles > 20) throw new RangeError("Log file count must be between 1 and 20");
}

/** Sink contract: size(), append(text), rotate(maxFiles), removeAll(),
 * readArchives(maxFiles, maxBytes), exportZip(destination, files).
 * maxFiles includes the active log; rotate must retain at most maxFiles - 1
 * archives. No sink operation occurs while disabled, except explicit delete
 * and user-requested export. Diagnostic I/O failures never abort browsing.
 */
export class DiagnosticsLogger {
  constructor({ sink, enabled = false, level = "warn", maxBytes = 1024 * 1024, maxFiles = 3, clock = () => new Date() }) {
    validateOptions({ level, maxBytes, maxFiles });
    if (!sink) throw new TypeError("Diagnostics sink is required");
    this.sink = sink;
    this.enabled = enabled === true;
    this.level = level;
    this.maxBytes = maxBytes;
    this.maxFiles = maxFiles;
    this.clock = clock;
    this.errorCount = 0;
    this.droppedRecordCount = 0;
    this._pendingRecords = 0;
    this._generation = 0;
    this._queue = Promise.resolve();
  }

  _serialize(operation) {
    const result = this._queue.then(operation);
    this._queue = result.catch(() => {});
    return result;
  }

  configure({ enabled = this.enabled, level = this.level, maxBytes = this.maxBytes, maxFiles = this.maxFiles } = {}) {
    validateOptions({ level, maxBytes, maxFiles });
    if (typeof enabled !== "boolean") throw new TypeError("Diagnostics enabled setting must be boolean");
    this.enabled = enabled;
    this.level = level;
    this.maxBytes = maxBytes;
    this.maxFiles = maxFiles;
    this._generation++;
  }

  log(event, data = {}, level = "info") {
    if (!this.enabled || !DIAGNOSTIC_EVENTS.includes(event) || !Object.hasOwn(LEVELS, level) || LEVELS[level] < LEVELS[this.level]) return Promise.resolve(false);
    // A broken page or extension can emit errors faster than disk I/O. Bound
    // queued records so optional diagnostics cannot exhaust browser memory.
    if (this._pendingRecords >= 256) {
      this.droppedRecordCount++;
      return Promise.resolve(false);
    }
    let record;
    try {
      record = sanitizeDiagnosticRecord({ version: 1, timestamp: this.clock().toISOString(), level, event, data });
    } catch { return Promise.resolve(false); }
    if (!record) return Promise.resolve(false);
    const line = `${JSON.stringify(record)}\n`;
    const bytes = utf8Length(line);
    const generation = this._generation;
    this._pendingRecords++;
    return this._serialize(async () => {
      try {
        if (!this.enabled || generation !== this._generation || bytes > this.maxBytes) return false;
        const currentSize = await this.sink.size();
        if (currentSize + bytes > this.maxBytes) await this.sink.rotate(this.maxFiles);
        await this.sink.append(line);
        return true;
      } catch {
        this.errorCount++;
        return false;
      } finally {
        this._pendingRecords--;
      }
    });
  }

  flush() { return this._queue; }

  deleteLogs() {
    // Cancel log calls queued before a deliberate deletion.
    this._generation++;
    return this._serialize(() => this.sink.removeAll());
  }

  exportPackage(destination, { userInitiated = false } = {}) {
    if (userInitiated !== true) return Promise.reject(new Error("Diagnostic export requires an explicit user action"));
    return this._serialize(async () => {
      const archives = await this.sink.readArchives(this.maxFiles, this.maxBytes);
      const records = [];
      // Re-sanitize existing files. Never bundle a raw profile, crash dump,
      // stack trace, preferences file, filesystem path, or modified log file.
      for (const archive of archives.slice(0, this.maxFiles)) {
        if (typeof archive !== "string") continue;
        for (const line of archive.slice(0, this.maxBytes).split("\n")) {
          if (line.length > 4096) continue;
          try {
            const record = sanitizeDiagnosticRecord(JSON.parse(line));
            if (record) records.push(JSON.stringify(record));
          } catch { /* malformed lines are excluded */ }
        }
      }
      const manifest = JSON.stringify({ schema: 1, product: "Privacy Browser", diagnosticsOnly: true, recordCount: records.length, ioErrorCount: this.errorCount, droppedRecordCount: this.droppedRecordCount }, null, 2);
      return this.sink.exportZip(destination, [
        { name: "manifest.json", contents: manifest },
        { name: "diagnostics.jsonl", contents: records.length ? `${records.join("\n")}\n` : "" },
      ]);
    });
  }
}

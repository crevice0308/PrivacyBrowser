/* SPDX-License-Identifier: MPL-2.0 */
/** Privacy policy shared by the Gecko integration and Node unit tests.
 * A disabled persistence flag means cleanup is mandatory, not that Gecko never
 * writes that category to disk. See PRIVACY_LIMITATIONS and README.md.
 */
export const HISTORY_KEYS = Object.freeze([
  "browsing", "search", "cookies", "downloads", "forms", "cache", "sessions", "siteStorage",
]);
export const DEFAULT_HISTORY_FLAGS = Object.freeze(Object.fromEntries(HISTORY_KEYS.map(key => [key, false])));
export const PRIVACY_LIMITATIONS = Object.freeze([
  "Cookie and site-storage data may be written during a session; startup and clean-shutdown cleanup do not protect against a crash or forced termination.",
  "Disabling session restore needs the packaged SessionStore guard to suppress live and final session files; preferences alone cannot guarantee this.",
  "Browser cleanup does not erase OS logs, DNS caches, swap, filesystem snapshots, backups, or forensic remnants of deleted files.",
  "A downloaded file is retained; its chosen filename and OS metadata are outside browser-history cleanup.",
]);

export function normalizeHistoryFlags(value = {}, base = DEFAULT_HISTORY_FLAGS) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("History flags must be an object");
  }
  for (const [key, enabled] of Object.entries(value)) {
    if (!HISTORY_KEYS.includes(key)) throw new TypeError(`Unknown history setting: ${key}`);
    if (typeof enabled !== "boolean") throw new TypeError(`History setting ${key} must be boolean`);
  }
  return Object.freeze(Object.fromEntries(HISTORY_KEYS.map(key => [key, value[key] ?? base[key] ?? false])));
}

/** No blanket permanent-private-browsing mode: settings remain independent. */
export function buildPreferencePlan(input = DEFAULT_HISTORY_FLAGS) {
  const flags = normalizeHistoryFlags(input);
  return Object.freeze({
    "places.history.enabled": flags.browsing,
    "browser.formfill.enable": flags.forms,
    "browser.cache.disk.enable": flags.cache,
    "browser.cache.disk.smart_size.enabled": flags.cache,
    "browser.cache.offline.enable": flags.cache,
    "browser.cache.memory.enable": true,
    "browser.sessionstore.resume_from_crash": flags.sessions,
    "browser.sessionstore.max_tabs_undo": flags.sessions ? 25 : 0,
    "browser.sessionstore.max_windows_undo": flags.sessions ? 3 : 0,
    "browser.sessionstore.privacy_level": 2,
    "browser.sessionstore.resume_session_once": false,
    "browser.startup.page": flags.sessions ? 3 : 1,
    "browser.download.manager.addToRecentDocs": flags.downloads,
    "browser.search.suggest.enabled": false,
    "browser.urlbar.suggest.searches": false,
    ...Object.fromEntries(HISTORY_KEYS.map(key => [`privacybrowser.history.${key}`, flags[key]])),
  });
}

export function categoriesToClear(input = DEFAULT_HISTORY_FLAGS) {
  const flags = normalizeHistoryFlags(input);
  return HISTORY_KEYS.filter(key => !flags[key]);
}

/**
 * Adapter contract: applyPreferences(object), clearCategory(key), setDirty(bool),
 * saveFlags(flags), readSearchHistory(), writeSearchHistory(array). The host must
 * await startup() before opening user pages, and register shutdown as an
 * AsyncShutdown blocker. All mutating operations are serialized.
 */
export class PrivacyController {
  constructor(adapter, flags = DEFAULT_HISTORY_FLAGS) {
    for (const name of ["applyPreferences", "clearCategory", "setDirty", "saveFlags"]) {
      if (typeof adapter?.[name] !== "function") throw new TypeError(`Missing privacy adapter method: ${name}`);
    }
    this.adapter = adapter;
    this.flags = normalizeHistoryFlags(flags);
    this.state = "new";
    this.lastFailures = [];
    this._queue = Promise.resolve();
  }

  _serialize(operation) {
    const pending = this._queue.then(operation);
    this._queue = pending.catch(() => {});
    return pending;
  }

  async _clear(categories) {
    const failures = [];
    const failureDetails = [];
    for (const category of categories) {
      try { await this.adapter.clearCategory(category); }
      catch (error) {
        failures.push(category);
        // Keep numeric Gecko failure flags and a fixed exception category for
        // local runtime inspection. Never retain an exception message/stack.
        const allowedNames = ["TypeError", "ReferenceError", "SyntaxError", "NotFoundError", "InvalidStateError", "SecurityError"];
        failureDetails.push(Object.freeze({ category, name: allowedNames.includes(error?.name) ? error.name : "Error", result: Number.isSafeInteger(error?.result) ? error.result : 0, failedFlags: Number.isSafeInteger(error?.failedFlags) ? error.failedFlags : 0 }));
      }
    }
    this.lastFailures = failures;
    if (failures.length) {
      const error = new Error(`Privacy cleanup failed for: ${failures.join(", ")}`);
      error.categories = Object.freeze(failures);
      error.failureDetails = Object.freeze(failureDetails);
      throw error;
    }
  }

  startup() {
    return this._serialize(async () => {
      if (this.state !== "new") throw new Error("Privacy startup already attempted");
      this.state = "starting";
      try {
        await this.adapter.setDirty(true);
        await this.adapter.applyPreferences(buildPreferencePlan(this.flags));
        // Always clean disabled categories, including after an unclean exit.
        await this._clear(categoriesToClear(this.flags));
        this.state = "ready";
        return this.snapshot();
      } catch (error) {
        this.state = "failed";
        throw error;
      }
    });
  }

  configure(changes) {
    return this._serialize(async () => {
      if (this.state !== "ready") throw new Error("Privacy controller is not ready");
      const next = normalizeHistoryFlags(changes, this.flags);
      const previous = this.flags;
      const restartRequired = next.sessions !== previous.sessions || next.cookies !== previous.cookies || next.siteStorage !== previous.siteStorage;
      // Persist the stricter policy before cleanup. A failed cleanup must never
      // silently revert to allowing persistence at the next startup.
      this.flags = next;
      try {
        await this.adapter.saveFlags(next);
        await this.adapter.applyPreferences(buildPreferencePlan(next));
        await this._clear(HISTORY_KEYS.filter(key => previous[key] && !next[key]));
      } catch (error) {
        this.state = "failed";
        throw error;
      }
      return Object.freeze({ ...this.snapshot(), restartRequired });
    });
  }

  clearAll() {
    return this._serialize(async () => {
      if (this.state !== "ready") throw new Error("Privacy controller is not ready");
      try { await this._clear(HISTORY_KEYS); }
      catch (error) { this.state = "failed"; throw error; }
      return this.snapshot();
    });
  }

  recordSearch(query) {
    return this._serialize(async () => {
      if (this.state !== "ready") throw new Error("Privacy controller is not ready");
      if (!this.flags.search) return false;
      if (typeof query !== "string" || !query.trim() || query.length > 4096) return false;
      const existing = await this.adapter.readSearchHistory();
      const history = [query, ...existing.filter(item => typeof item === "string" && item !== query)].slice(0, 1000);
      await this.adapter.writeSearchHistory(history);
      return true;
    });
  }

  readSearchHistory() {
    return this._serialize(async () => {
      if (this.state !== "ready") throw new Error("Privacy controller is not ready");
      return this.flags.search ? [...await this.adapter.readSearchHistory()] : [];
    });
  }

  shutdown() {
    return this._serialize(async () => {
      if (this.state === "closed") return this.snapshot();
      if (this.state === "new") throw new Error("Privacy controller was never started");
      this.state = "closing";
      try {
        await this._clear(categoriesToClear(this.flags));
        await this.adapter.setDirty(false);
        this.state = "closed";
        return this.snapshot();
      } catch (error) {
        this.state = "failed";
        throw error;
      }
    });
  }

  snapshot() {
    return Object.freeze({ state: this.state, flags: this.flags, failedCategories: Object.freeze([...this.lastFailures]) });
  }
}

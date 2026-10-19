/* SPDX-License-Identifier: MPL-2.0 */
import { HISTORY_KEYS, PrivacyController } from "./PrivacyCore.sys.mjs";
import { DiagnosticsLogger } from "./Diagnostics.sys.mjs";

const GeckoServices = Services; // Built-in system-module global; Services.sys.mjs was removed upstream.
const lazy = {};
ChromeUtils.defineESModuleGetters(lazy, {
  FormHistory: "resource://gre/modules/FormHistory.sys.mjs",
  PlacesUtils: "resource://gre/modules/PlacesUtils.sys.mjs",
});
const profilePath = () => GeckoServices.dirsvc.get("ProfD", Ci.nsIFile).path;
const searchPath = () => PathUtils.join(profilePath(), "privacybrowser-search-history.json");
const missing = error => error?.name === "NotFoundError";
const remove = async path => { await IOUtils.remove(path, { ignoreAbsent: true }); };

export class GeckoPrivacyAdapter {
  async applyPreferences(plan) {
    for (const [key, value] of Object.entries(plan)) {
      if (typeof value === "boolean") GeckoServices.prefs.setBoolPref(key, value);
      else if (Number.isInteger(value)) GeckoServices.prefs.setIntPref(key, value);
      else GeckoServices.prefs.setStringPref(key, value);
    }
    GeckoServices.prefs.savePrefFile(null);
  }
  async setDirty(value) {
    if (value) GeckoServices.prefs.setBoolPref("privacybrowser.previousUnclean", GeckoServices.prefs.getBoolPref("privacybrowser.uncleanExit", false));
    GeckoServices.prefs.setBoolPref("privacybrowser.uncleanExit", value);
    GeckoServices.prefs.savePrefFile(null);
  }
  async saveFlags(flags) {
    for (const key of HISTORY_KEYS) GeckoServices.prefs.setBoolPref(`privacybrowser.history.${key}`, flags[key]);
    GeckoServices.prefs.savePrefFile(null);
  }
  async readSearchHistory() {
    try {
      const list = await IOUtils.readJSON(searchPath());
      return Array.isArray(list) ? list.filter(item => typeof item === "string" && item.length <= 4096).slice(0, 1000) : [];
    } catch (error) {
      if (missing(error)) return [];
      throw error;
    }
  }
  async writeSearchHistory(history) {
    const path = searchPath();
    await IOUtils.writeJSON(path, history, { tmpPath: `${path}.tmp` });
    await IOUtils.setPermissions(path, 0o600);
  }
  async clearCategory(category) {
    if (category === "browsing") {
      // CLEAR_HISTORY also broadcasts browser:purge-session-history, which
      // wipes independently retained sessions and can race SessionFile.read at
      // startup. Use the same narrow API as upstream HistoryCleaner instead.
      // Places retains bookmarked entries while clearing their visit records.
      await lazy.PlacesUtils.history.clear();
      return;
    }
    if (category === "search") {
      await remove(searchPath());
      await remove(`${searchPath()}.tmp`);
      await lazy.FormHistory.update({ op: "remove", fieldname: "searchbar-history" });
      return;
    }
    if (category === "forms") {
      await lazy.FormHistory.update({ op: "remove" });
      return;
    }
    if (category === "sessions") {
      // These files are the browser's own session records, never bookmarks or
      // downloaded files. A source-level SessionStore guard is still needed to
      // prevent later writes; wiping files is not a memory-only implementation.
      for (const name of ["sessionstore.jsonlz4", "sessionstore.js", "sessionstore.bak", "sessionstore.baklz4"]) {
        await remove(PathUtils.join(profilePath(), name));
      }
      await IOUtils.remove(PathUtils.join(profilePath(), "sessionstore-backups"), { ignoreAbsent: true, recursive: true });
      return;
    }
    const flags = Ci.nsIClearDataService;
    const mapping = {
      cookies: flags.CLEAR_COOKIES,
      downloads: flags.CLEAR_DOWNLOADS,
      cache: flags.CLEAR_NETWORK_CACHE | flags.CLEAR_IMAGE_CACHE | flags.CLEAR_JS_CACHE | (flags.CLEAR_CSS_CACHE || 0),
      siteStorage: flags.CLEAR_DOM_STORAGES | (flags.CLEAR_EME || 0),
    };
    if (!Object.hasOwn(mapping, category) || !mapping[category]) throw new Error("Unsupported cleanup category");
    await new Promise((resolve, reject) => {
      GeckoServices.clearData.deleteData(mapping[category], {
        onDataDeleted(failedFlags) {
          if (failedFlags) {
            const error = new Error("Gecko data cleanup failed");
            error.failedFlags = failedFlags;
            reject(error);
          }
          else resolve();
        },
      });
    });
  }
}

/** Singleton used by BrowserGlue. Calls do not install hidden timers. */
export const PrivacyBrowserPrivacy = {
  controller: null,
  _startupPromise: null,
  _configuring: 0,
  _observer: null,
  _queued: false,
  get flags() { return this.controller?.flags || Object.fromEntries(HISTORY_KEYS.map(key => [key, false])); },
  startup() {
    if (this._startupPromise) return this._startupPromise;
    const flags = Object.fromEntries(HISTORY_KEYS.map(key => [key, GeckoServices.prefs.getBoolPref(`privacybrowser.history.${key}`, false)]));
    this.controller = new PrivacyController(new GeckoPrivacyAdapter(), flags);
    this._startupPromise = this.controller.startup().then(snapshot => {
      this._observer = { observe: (_subject, _topic, key) => {
        const flag = key.replace("privacybrowser.history.", "");
        if (this._queued || !HISTORY_KEYS.includes(flag) || GeckoServices.prefs.getBoolPref(key, false) === this.controller.flags[flag]) return;
        this._queued = true;
        Promise.resolve().then(async () => {
          this._queued = false;
          const next = Object.fromEntries(HISTORY_KEYS.map(name => [name, GeckoServices.prefs.getBoolPref(`privacybrowser.history.${name}`, false)]));
          await this.configure(next);
        }).catch(() => GeckoServices.obs.notifyObservers(null, "privacybrowser:privacy-error", "cleanup"));
      } };
      GeckoServices.prefs.addObserver("privacybrowser.history.", this._observer);
      return snapshot;
    });
    return this._startupPromise;
  },
  async configure(changes) {
    this._configuring++;
    try { return await this.controller.configure(changes); }
    finally { this._configuring--; }
  },
  clearAll() { return this.controller.clearAll(); },
  recordSearch(query) { return this.controller.recordSearch(query); },
  readSearchHistory() { return this.controller.readSearchHistory(); },
  shutdown() {
    if (this._observer) GeckoServices.prefs.removeObserver("privacybrowser.history.", this._observer);
    this._observer = null;
    return this.controller?.shutdown();
  },
};

export class GeckoDiagnosticsSink {
  constructor(directory = "") {
    if (directory && !PathUtils.isAbsolute(directory)) throw new TypeError("Diagnostic directory must be absolute");
    this.directory = directory || PathUtils.join(profilePath(), "privacybrowser-diagnostics");
  }
  path(index = 0) { return PathUtils.join(this.directory, index ? `diagnostics.${index}.jsonl` : "diagnostics.jsonl"); }
  async ensureDirectory() { await IOUtils.makeDirectory(this.directory, { ignoreExisting: true, permissions: 0o700 }); }
  async size() {
    try { return (await IOUtils.stat(this.path())).size; }
    catch (error) { if (missing(error)) return 0; throw error; }
  }
  async append(text) {
    await this.ensureDirectory();
    await IOUtils.writeUTF8(this.path(), text, { mode: "append" });
    await IOUtils.setPermissions(this.path(), 0o600);
  }
  async rotate(maxFiles) {
    await this.ensureDirectory();
    for (let index = 19; index >= maxFiles - 1; index--) await remove(this.path(index));
    for (let index = maxFiles - 2; index >= 0; index--) {
      if (await IOUtils.exists(this.path(index))) await IOUtils.move(this.path(index), this.path(index + 1));
    }
  }
  async removeAll() { for (let index = 0; index < 20; index++) await remove(this.path(index)); }
  async readArchives(maxFiles, maxBytes) {
    const output = [];
    for (let index = maxFiles - 1; index >= 0; index--) {
      try {
        if ((await IOUtils.stat(this.path(index))).size > maxBytes) continue;
        output.push(await IOUtils.readUTF8(this.path(index)));
      } catch (error) { if (!missing(error)) throw error; }
    }
    return output;
  }
  async exportZip(destination, files) {
    if (typeof destination !== "string" || !PathUtils.isAbsolute(destination)) throw new TypeError("Export destination must be absolute");
    const temporary = `${destination}.${GeckoServices.uuid.generateUUID().toString().replace(/[{}]/g, "")}.tmp`;
    const file = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
    file.initWithPath(temporary);
    const writer = Cc["@mozilla.org/zipwriter;1"].createInstance(Ci.nsIZipWriter);
    let opened = false;
    try {
      writer.open(file, 0x02 | 0x08 | 0x80); // WRONLY | CREATE_FILE | EXCL
      opened = true;
      for (const entry of files) {
        if (!["manifest.json", "diagnostics.jsonl"].includes(entry.name)) throw new Error("Unsupported package entry");
        const stream = Cc["@mozilla.org/io/string-input-stream;1"].createInstance(Ci.nsIStringInputStream);
        stream.setUTF8Data(entry.contents);
        writer.addEntryStream(entry.name, Date.now() * 1000, Ci.nsIZipWriter.COMPRESSION_DEFAULT, stream, false);
      }
      writer.close();
      opened = false;
      await IOUtils.setPermissions(temporary, 0o600);
      await IOUtils.move(temporary, destination, { noOverwrite: false });
      return destination;
    } catch (error) {
      if (opened) { try { writer.close(); } catch {} }
      await remove(temporary);
      throw error;
    }
  }
}

export function createGeckoDiagnostics() {
  const read = () => ({
    enabled: GeckoServices.prefs.getBoolPref("privacybrowser.diagnostics.enabled", false),
    level: GeckoServices.prefs.getStringPref("privacybrowser.diagnostics.level", "warn"),
    maxBytes: GeckoServices.prefs.getIntPref("privacybrowser.diagnostics.maxBytes", 1024 * 1024),
    maxFiles: GeckoServices.prefs.getIntPref("privacybrowser.diagnostics.maxFiles", 3),
  });
  const directory = () => GeckoServices.prefs.getStringPref("privacybrowser.diagnostics.directory", "");
  const logger = new DiagnosticsLogger({ sink: new GeckoDiagnosticsSink(directory()), ...read() });
  let refresh = Promise.resolve();
  let disposed = false;
  const observer = { observe() {
    // Stop queued writes immediately. Change a directory only after the prior
    // sink has drained, so a pending operation cannot write into the new sink.
    logger.configure({ enabled: false });
    refresh = refresh.then(async () => {
      await logger.flush();
      if (disposed) return;
      logger.sink = new GeckoDiagnosticsSink(directory());
      logger.configure(read());
    }).catch(() => GeckoServices.obs.notifyObservers(null, "privacybrowser:diagnostics-error", "configuration"));
  } };
  GeckoServices.prefs.addObserver("privacybrowser.diagnostics.", observer);
  logger.dispose = async () => {
    disposed = true;
    GeckoServices.prefs.removeObserver("privacybrowser.diagnostics.", observer);
    await refresh;
    await logger.flush();
  };
  return logger;
}

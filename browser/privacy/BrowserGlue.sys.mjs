/* SPDX-License-Identifier: MPL-2.0 */
import { PrivacyBrowserPrivacy, createGeckoDiagnostics, GeckoDiagnosticsSink } from "resource://privacybrowser/GeckoPrivacy.sys.mjs";
import { initWindowUI } from "resource://privacybrowser/WindowUI.sys.mjs";
import { initTaskManager, flattenProcesses, cpuPercent } from "resource://privacybrowser/TaskManager.sys.mjs";
import { initWindow as initLifecycle } from "resource://privacybrowser/Lifecycle.sys.mjs";
import { AsyncShutdown } from "resource://gre/modules/AsyncShutdown.sys.mjs";

let started = false;
let ready;
let diagnostics = null;
let samplingTimer = null;
const cleanups = [];
const topics = ["privacybrowser:clear-data", "privacybrowser:search-performed", "privacybrowser:diagnostics-export", "privacybrowser:diagnostics-delete", "privacybrowser:diagnostics-choose-directory"];

function reportFailure() {
  // Error messages and stacks can contain browsing data. Never copy them into
  // diagnostics, alerts, or persisted application logs.
  diagnostics?.log("internal-exception", { code: "api" }, "error");
}

async function picker(mode, title) {
  const win = Services.wm.getMostRecentWindow("navigator:browser");
  if (!win) return null;
  const fp = Cc["@mozilla.org/filepicker;1"].createInstance(Ci.nsIFilePicker);
  fp.init(win.browsingContext, title, mode);
  if (mode === Ci.nsIFilePicker.modeSave) {
    fp.defaultString = "privacy-browser-diagnostics.zip";
    fp.appendFilter("ZIP archive", "*.zip");
  }
  const result = await new Promise(resolve => fp.open(resolve));
  return result === Ci.nsIFilePicker.returnCancel ? null : fp.file;
}

const actionObserver = {
  observe(_subject, topic, data) {
    void ready.then(async () => {
      switch (topic) {
        case "privacybrowser:clear-data":
          await PrivacyBrowserPrivacy.clearAll(); break;
        case "privacybrowser:search-performed":
          await PrivacyBrowserPrivacy.recordSearch(data); break;
        case "privacybrowser:diagnostics-delete":
          await diagnostics?.deleteLogs(); break;
        case "privacybrowser:diagnostics-choose-directory": {
          const directory = await picker(Ci.nsIFilePicker.modeGetFolder, "Diagnostic log folder");
          if (directory) Services.prefs.setStringPref("privacybrowser.diagnostics.directory", directory.path);
          break;
        }
        case "privacybrowser:diagnostics-export": {
          const file = await picker(Ci.nsIFilePicker.modeSave, "Export diagnostics");
          if (file) await diagnostics?.exportPackage(file.path, { userInitiated: true });
          break;
        }
      }
    }).catch(reportFailure);
  },
};

async function attach(win) {
  await ready;
  if (win.closed || win.__privacyBrowserAttached) return;
  win.__privacyBrowserAttached = true;
  for (const initialize of [initWindowUI, initTaskManager, initLifecycle]) {
    try {
      const stop = initialize(win);
      if (typeof stop === "function") win.addEventListener("unload", stop, { once: true });
    } catch { reportFailure(); }
  }
}

const windowObserver = {
  observe(win) { void attach(win).catch(reportFailure); },
};

export function startup() {
  if (started) return ready;
  started = true;
  // Set before starting async work; UI is attached only after cleanup succeeds.
  ready = (async () => {
    await PrivacyBrowserPrivacy.startup();
    const { AboutNewTab } = ChromeUtils.importESModule("resource:///modules/AboutNewTab.sys.mjs");
    AboutNewTab.newTabURL = "chrome://privacybrowser/content/home.html";
    diagnostics = createGeckoDiagnostics();
    startResourceSampling();
    await diagnostics.log("startup", { recovered: Services.prefs.getBoolPref("privacybrowser.previousUnclean", false) }, "info");
  })();
  AsyncShutdown.appShutdownConfirmed.addBlocker("Privacy Browser: clear disabled persistence and finish diagnostics", async () => {
    try {
      await ready;
      if (samplingTimer) samplingTimer.cancel();
      await PrivacyBrowserPrivacy.shutdown();
      await diagnostics?.log("shutdown", { cleanExit: true }, "info");
      await diagnostics?.flush();
      await diagnostics?.dispose?.();
    } finally {
      for (const stop of cleanups) stop();
    }
  });
  Services.obs.addObserver(windowObserver, "browser-delayed-startup-finished");
  cleanups.push(() => Services.obs.removeObserver(windowObserver, "browser-delayed-startup-finished"));
  for (const topic of topics) Services.obs.addObserver(actionObserver, topic);
  cleanups.push(() => { for (const topic of topics) Services.obs.removeObserver(actionObserver, topic); });
  for (const win of Services.wm.getEnumerator("navigator:browser")) {
    if (win.gBrowserInit?.delayedStartupFinished) void attach(win).catch(reportFailure);
  }
  // No console text, exception messages, stack traces, extension IDs, or URLs.
  const consoleObserver = { observe(message) {
    if (!diagnostics?.enabled) return;
    try {
      const scriptError = message.QueryInterface(Ci.nsIScriptError);
      if (scriptError.isFromPrivateWindow) return;
      diagnostics.log("internal-exception", { code: "api" }, "error");
    } catch { /* non-script console messages are not retained */ }
  } };
  Services.console.registerListener(consoleObserver);
  cleanups.push(() => Services.console.unregisterListener(consoleObserver));
  return ready;
}

export function getStatus() {
  return { started, privacy: PrivacyBrowserPrivacy.controller?.snapshot(), diagnosticsEnabled: diagnostics?.enabled ?? false };
}

let lastSample = null;
let lastSampleTime = 0;
let sampling = false;
function startResourceSampling() {
  samplingTimer = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
  samplingTimer.initWithCallback({ notify() { void sampleResources(); } }, 5000, Ci.nsITimer.TYPE_REPEATING_SLACK);
  const crashObserver = { observe(subject) {
    try {
      const bag = subject.QueryInterface(Ci.nsIPropertyBag2);
      if (bag.getPropertyAsBool("abnormal")) diagnostics?.log("crash", { cleanExit: false }, "error");
    } catch { /* No raw crash annotations, minidumps, or process names are read. */ }
  } };
  Services.obs.addObserver(crashObserver, "ipc:content-shutdown");
  cleanups.push(() => Services.obs.removeObserver(crashObserver, "ipc:content-shutdown"));
}
async function sampleResources() {
  if (!diagnostics?.enabled || sampling) return;
  sampling = true;
  try {
    const snapshot = await ChromeUtils.requestProcInfo();
    const now = Cu.now();
    const previous = new Map(flattenProcesses(lastSample).map(p => [p.pid, p]));
    for (const process of flattenProcesses(snapshot)) {
      const cpu = cpuPercent(process, previous.get(process.pid), now - lastSampleTime);
      await diagnostics.log("resource-sample", { pid: Number(process.pid), residentBytes: Number(process.memory), ...(cpu === null ? {} : { cpuPercent: cpu }) }, "debug");
    }
    lastSample = snapshot; lastSampleTime = now;
  } catch { reportFailure(); }
  finally { sampling = false; }
}

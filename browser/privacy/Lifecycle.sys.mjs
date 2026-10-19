/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. See https://mozilla.org/MPL/2.0/. */

/** Graceful full exit and conservative idle tab unloading, using Gecko APIs. */
const states = new WeakMap();
let quitInProgress = false;

function getServices() {
  return Services;
}

export function shouldQuitOnClose({ completeExit, isLast, quitting = false, defaultPrevented = false }) {
  return Boolean(completeExit && isLast && !quitting && !defaultPrevented);
}

/** Do not inspect or return field contents; only decide whether to preserve a tab. */
export function hasFormData(state) {
  if (!state || typeof state !== "object") return false;
  if (state.formdata && Object.keys(state.formdata).length) return true;
  return (state.entries || []).some(hasFormData) || (state.children || []).some(hasFormData);
}

/** Unknown data is ineligible, so API failures never cause aggressive unloading. */
export function canSuspendTab(tab, { now, idleMs }) {
  if (!tab || !Number.isFinite(now) || !Number.isFinite(idleMs) || idleMs <= 0 ||
      !Number.isFinite(tab.lastAccessed)) return false;
  return Boolean(tab.connected && tab.remote && tab.webPage &&
    !tab.selected && !tab.pinned && !tab.pending && !tab.busy && !tab.audio &&
    !tab.sharing && !tab.formData && !tab.downloadsActive &&
    now - tab.lastAccessed >= idleMs);
}

function browserWindowCount() {
  let count = 0;
  for (const win of getServices().wm.getEnumerator("navigator:browser")) {
    if (!win.closed) count++;
  }
  return count;
}

function tabMetadata(win, tab, activeDownloads, formData) {
  const browser = tab.linkedBrowser;
  const scheme = browser?.currentURI?.scheme;
  return {
    connected: Boolean(tab.isConnected),
    remote: Boolean(browser?.isRemoteBrowser),
    webPage: scheme === "http" || scheme === "https",
    selected: tab === win.gBrowser.selectedTab,
    pinned: Boolean(tab.pinned),
    pending: tab.hasAttribute("pending"),
    busy: tab.hasAttribute("busy"),
    audio: tab.hasAttribute("soundplaying") || tab.hasAttribute("activemedia-blocked"),
    sharing: Boolean(tab._sharingState?.webRTC || tab._sharingState?.camera ||
      tab._sharingState?.microphone || tab._sharingState?.screen ||
      browser?.browsingContext?.currentWindowGlobal?.hasActivePeerConnections?.()),
    formData,
    downloadsActive: activeDownloads,
    lastAccessed: tab.lastAccessed,
  };
}

async function suspendOneTab(win, state, underPressure = false) {
  const services = getServices();
  if (state.suspending || win.closed || !services.prefs.getBoolPref("privacybrowser.tabs.suspend.enabled", true)) return;
  const minutes = services.prefs.getIntPref("privacybrowser.tabs.suspend.minutes", 15);
  if (minutes <= 0) return;
  state.suspending = true;
  try {
    const { Downloads } = ChromeUtils.importESModule("resource://gre/modules/Downloads.sys.mjs");
    const downloads = await (await Downloads.getList(Downloads.ALL)).getAll();
    // Conservatively preserve every tab during an active download; Gecko may
    // associate a download with a subframe rather than the visible top-level tab.
    if (downloads.some(download => !download.stopped)) return;
    const { SessionStore } = ChromeUtils.importESModule("resource:///modules/sessionstore/SessionStore.sys.mjs");
    const { TabStateFlusher } = ChromeUtils.importESModule("resource:///modules/sessionstore/TabStateFlusher.sys.mjs");
    const idleMs = underPressure ? Math.min(minutes * 60_000, 60_000) : minutes * 60_000;
    const candidates = [...win.gBrowser.tabs].sort((a, b) => a.lastAccessed - b.lastAccessed);
    for (const tab of candidates) {
      if (!canSuspendTab(tabMetadata(win, tab, false, false), { now: Date.now(), idleMs })) continue;
      // Pull pending form changes into Gecko's in-memory session state. Never
      // write the returned data ourselves, log it, or include it in diagnostics.
      await TabStateFlusher.flush(tab.linkedBrowser);
      if (win.closed || !tab.isConnected) return;
      const sessionState = JSON.parse(SessionStore.getTabState(tab));
      const metadata = tabMetadata(win, tab, false, hasFormData(sessionState));
      if (!canSuspendTab(metadata, { now: Date.now(), idleMs })) continue;
      // Never force discard: Gecko additionally checks permitUnload and its own
      // safety conditions. SessionStore keeps this tab available to restore.
      if (win.gBrowser.discardBrowser(tab, false)) break;
    }
  } catch {
    // An unavailable API, a tab closed during await, or a failed state flush
    // means no tab is unloaded. The next timer or memory event can retry.
  } finally { state.suspending = false; }
}

export function destroyWindow(win) {
  const state = states.get(win);
  if (!state) return;
  win.clearInterval(state.timer);
  win.removeEventListener("close", state.onClose);
  getServices().obs.removeObserver(state.memoryObserver, "memory-pressure");
  states.delete(win);
}

export function initWindow(win) {
  if (states.has(win)) return;
  const services = getServices();
  const state = { suspending: false, timer: null, onClose: null, memoryObserver: null };
  states.set(win, state);
  // Initialize the native download integration early. Its
  // quit-application-requested observer prompts before cancelling live downloads.
  try {
    const { Downloads } = ChromeUtils.importESModule("resource://gre/modules/Downloads.sys.mjs");
    Downloads.getList(Downloads.ALL).catch(() => {});
  } catch { /* Native browser integration still applies on an API mismatch. */ }
  state.onClose = event => {
    if (!shouldQuitOnClose({
      completeExit: services.prefs.getBoolPref("privacybrowser.completeQuit", true),
      isLast: browserWindowCount() === 1,
      quitting: quitInProgress,
      defaultPrevented: event.defaultPrevented,
    })) return;
    event.preventDefault();
    quitInProgress = true;
    try {
      // nsAppStartup.quit() checks CanClose but does not emit this vetoable
      // observer topic. Match Firefox's quit command before entering shutdown.
      const cancelQuit = Cc["@mozilla.org/supports-PRBool;1"].createInstance(Ci.nsISupportsPRBool);
      services.obs.notifyObservers(cancelQuit, "quit-application-requested", null);
      if (cancelQuit.data) return;
      // Do not use eForceQuit, OS kill, or bypass DownloadIntegration prompts.
      services.startup.quit(Ci.nsIAppStartup.eAttemptQuit);
    } finally {
      // A user may veto shutdown, leaving the window fully usable.
      quitInProgress = false;
    }
  };
  win.addEventListener("close", state.onClose);
  state.memoryObserver = {
    observe(_subject, _topic, data) {
      if (data === "low-memory" || data === "low-memory-ongoing") suspendOneTab(win, state, true);
    },
  };
  services.obs.addObserver(state.memoryObserver, "memory-pressure");
  state.timer = win.setInterval(() => suspendOneTab(win, state), 60_000);
  win.addEventListener("unload", () => destroyWindow(win), { once: true });
}

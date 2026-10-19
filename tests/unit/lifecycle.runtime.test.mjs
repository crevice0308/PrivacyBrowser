import test from "node:test";
import assert from "node:assert/strict";
import { initWindow, destroyWindow } from "../../browser/privacy/Lifecycle.sys.mjs";

function runtime(t) {
  const windows = [], observers = new Map(), preferences = new Map();
  const calls = { quits: [], notifications: [], flushes: [], discards: [], imports: [] };
  const downloads = [];
  const services = {
    wm: { getEnumerator: () => windows },
    prefs: { getBoolPref: (key, fallback) => preferences.get(key) ?? fallback,
      getIntPref: (key, fallback) => preferences.get(key) ?? fallback },
    obs: {
      addObserver(observer, topic) {
        if (!observers.has(topic)) observers.set(topic, new Set());
        observers.get(topic).add(observer);
      },
      removeObserver(observer, topic) { observers.get(topic)?.delete(observer); },
      notifyObservers(subject, topic, data) {
        calls.notifications.push(topic);
        for (const observer of observers.get(topic) || []) observer.observe(subject, topic, data);
      },
    },
    startup: { quit(mode) { calls.quits.push(mode); } },
  };
  const previous = Object.fromEntries(["ChromeUtils", "Ci", "Cc", "Services"].map(key => [key, globalThis[key]]));
  globalThis.Services = services;
  globalThis.ChromeUtils = { importESModule(uri) {
    calls.imports.push(uri);
    if (uri.endsWith("/Downloads.sys.mjs")) return {
      Downloads: { ALL: 1, getList: async () => ({ getAll: async () => downloads }) },
    };
    if (uri.endsWith("/SessionStore.sys.mjs")) return { SessionStore: { getTabState: tab => JSON.stringify(tab.state || {}) } };
    if (uri.endsWith("/TabStateFlusher.sys.mjs")) return { TabStateFlusher: { flush: async browser => { calls.flushes.push(browser); } } };
    throw new Error("Unexpected native import");
  } };
  globalThis.Ci = { nsIAppStartup: { eAttemptQuit: 1 }, nsISupportsPRBool: "boolean" };
  globalThis.Cc = { "@mozilla.org/supports-PRBool;1": { createInstance: () => ({ data: false }) } };
  function makeWindow() {
    const listeners = new Map();
    const win = { closed: false, gBrowser: { tabs: [], selectedTab: null,
      discardBrowser(tab, force) { calls.discards.push({ tab, force }); return true; } },
      addEventListener(event, handler) {
        if (!listeners.has(event)) listeners.set(event, new Set());
        listeners.get(event).add(handler);
      },
      removeEventListener(event, handler) { listeners.get(event)?.delete(handler); },
      setInterval(callback) { this.tick = callback; return 1; },
      clearInterval() { this.tick = null; },
      closeEvent(defaultPrevented = false) {
        const event = { defaultPrevented, preventDefault() { this.defaultPrevented = true; } };
        for (const listener of listeners.get("close") || []) listener(event);
        return event;
      },
    };
    windows.push(win);
    return win;
  }
  t.after(() => {
    for (const win of windows) destroyWindow(win);
    for (const key of Object.keys(previous)) {
      if (previous[key] === undefined) delete globalThis[key]; else globalThis[key] = previous[key];
    }
  });
  return { makeWindow, calls, observers, services, preferences, downloads };
}

test("native download quit veto is notified before startup.quit, and cancellation permits retry", t => {
  const environment = runtime(t);
  const win = environment.makeWindow();
  let veto = true;
  environment.services.obs.addObserver({ observe(subject) { subject.data = veto; } }, "quit-application-requested");
  initWindow(win);
  assert.ok(environment.calls.imports.every(uri => !uri.endsWith("/Services.sys.mjs")),
    "ESR153 exposes Services as a system global, not an importable module");
  assert.equal(win.closeEvent().defaultPrevented, true);
  assert.deepEqual(environment.calls.quits, []);
  veto = false;
  assert.equal(win.closeEvent().defaultPrevented, true);
  assert.deepEqual(environment.calls.notifications, ["quit-application-requested", "quit-application-requested"]);
  assert.deepEqual(environment.calls.quits, [1]);
});

test("another browser window, disabled full exit, and earlier close cancellation suppress quit", t => {
  const environment = runtime(t);
  const one = environment.makeWindow(), two = environment.makeWindow();
  initWindow(one);
  assert.equal(one.closeEvent().defaultPrevented, false);
  two.closed = true;
  environment.preferences.set("privacybrowser.completeQuit", false);
  assert.equal(one.closeEvent().defaultPrevented, false);
  environment.preferences.set("privacybrowser.completeQuit", true);
  one.closeEvent(true);
  assert.deepEqual(environment.calls.quits, []);
  assert.deepEqual(environment.calls.notifications, []);
});

test("runtime unloading flushes state and uses non-forced Gecko discard while preserving forms", async t => {
  const environment = runtime(t), win = environment.makeWindow();
  const tab = state => ({ isConnected: true, pinned: false, lastAccessed: Date.now() - 3_600_000,
    linkedBrowser: { isRemoteBrowser: true, currentURI: { scheme: "https" },
      browsingContext: { currentWindowGlobal: { hasActivePeerConnections: () => false } } },
    hasAttribute: () => false, state });
  const form = tab({ formdata: { id: { input: "private value" } } }), idle = tab({});
  win.gBrowser.tabs = [form, idle];
  initWindow(win);
  await win.tick();
  assert.equal(environment.calls.flushes.length, 2);
  assert.deepEqual(environment.calls.discards, [{ tab: idle, force: false }]);
  environment.calls.discards.length = 0;
  environment.downloads.push({ stopped: false });
  await win.tick();
  assert.deepEqual(environment.calls.discards, []);
  environment.downloads.length = 0;
  idle.linkedBrowser.browsingContext.currentWindowGlobal.hasActivePeerConnections = () => true;
  await win.tick();
  assert.deepEqual(environment.calls.discards, []);
});

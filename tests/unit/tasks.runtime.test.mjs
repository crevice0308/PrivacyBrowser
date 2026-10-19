import test from "node:test";
import assert from "node:assert/strict";
import { initTaskManager, openTaskManager, TASK_PAGE } from "../../browser/privacy/TaskManager.sys.mjs";

test("task window uses ESR153 Services global, reuses its dialog, and reopens for a context tab", t => {
  const previous = { Services: globalThis.Services, ChromeUtils: globalThis.ChromeUtils };
  const imports = [], dialogs = [];
  const tab = { label: "Example", linkedBrowser: { currentURI: { spec: "https://example.test" },
    browsingContext: { currentWindowGlobal: { osPid: 123 } } }, hasAttribute: () => false };
  const win = { gBrowser: { tabs: [tab] }, openDialog(url) {
    const dialog = { url, closed: false, focused: false,
      addEventListener() {}, focus() { this.focused = true; }, close() { this.closed = true; } };
    dialogs.push(dialog);
    return dialog;
  } };
  globalThis.Services = { wm: { getEnumerator: () => [win] } };
  globalThis.ChromeUtils = { importESModule(uri) { imports.push(uri); throw new Error("Unexpected module import"); } };
  t.after(() => {
    for (const key of Object.keys(previous)) {
      if (previous[key] === undefined) delete globalThis[key]; else globalThis[key] = previous[key];
    }
  });
  initTaskManager(win);
  const first = openTaskManager(win);
  assert.equal(first.url, TASK_PAGE);
  assert.equal(openTaskManager(win), first);
  assert.equal(first.focused, true);
  const contextual = openTaskManager(win, tab);
  assert.equal(first.closed, true);
  assert.notEqual(contextual, first);
  assert.equal(dialogs.length, 2);
  assert.deepEqual(imports, [], "Services.sys.mjs is unavailable on ESR153");
});

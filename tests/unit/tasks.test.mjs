import test from "node:test";
import assert from "node:assert/strict";
import { buildTaskRows, cpuPercent, flattenProcesses, isProtectedProcess, sortTaskRows }
  from "../../browser/privacy/TaskManager.sys.mjs";

test("CPU sampling uses nanosecond deltas and permits multi-core totals above 100%", () => {
  assert.equal(cpuPercent({ cpuTime: 3_500_000_000 }, { cpuTime: 500_000_000 }, 2000), 150);
  assert.equal(cpuPercent({ cpuTime: 1 }, null, 2000), null);
  assert.equal(cpuPercent({ cpuTime: 1 }, { cpuTime: 2 }, 2000), null);
  assert.equal(cpuPercent({ cpuTime: 5 }, { cpuTime: 2 }, 0), null);
  assert.equal(cpuPercent({}, { cpuTime: 2 }, 1000), null);
});

test("shared tabs do not receive duplicated process CPU or RAM allocations", () => {
  const snapshot = { pid: 10, type: "browser", memory: 1000, cpuTime: 100,
    children: [{ pid: 20, type: "webIsolated", memory: 8000, cpuTime: 1_000_000_000 }] };
  const previousSnapshot = { pid: 10, type: "browser", cpuTime: 100,
    children: [{ pid: 20, type: "webIsolated", cpuTime: 0 }] };
  const tabs = [{ id: "tab-a", name: "One", pid: 20 }, { id: "tab-b", name: "Two", pid: 20 }];
  const rows = buildTaskRows({ snapshot, previousSnapshot, elapsedMs: 1000, tabs });
  const process = rows.find(row => row.id === "process-20");
  assert.equal(process.cpu, 100);
  assert.equal(process.memory, 8000);
  assert.match(process.state, /Shared by 2/);
  for (const tab of rows.filter(row => row.kind === "tab")) {
    assert.equal(tab.cpu, null);
    assert.equal(tab.memory, null);
    assert.match(tab.state, /shared PID/);
  }
});

test("internal processes and chrome tabs remain protected and discarded tabs remain visible", () => {
  const rows = buildTaskRows({
    snapshot: { pid: 10, type: "browser", children: [{ pid: 30, type: "extension" }] },
    tabs: [{ id: "chrome", pid: 10, name: "Settings", protected: true },
      { id: "pending", pid: null, name: "Saved tab", pending: true }],
  });
  assert.equal(rows.find(row => row.id === "process-10").protected, true);
  assert.equal(rows.find(row => row.id === "process-30").protected, true);
  assert.equal(rows.find(row => row.id === "chrome").protected, true);
  assert.equal(rows.find(row => row.id === "pending").state, "Suspended");
  for (const type of ["gpu", "socket", "rdd", "utility", "unknown", "privilegedabout"]) {
    assert.equal(isProtectedProcess(type), true);
  }
  assert.equal(isProtectedProcess("webIsolated"), false);
});

test("resource sorting places unavailable tab measurements last without mutating input", () => {
  const rows = [{ id: "tab", name: "A", cpu: null, memory: null },
    { id: "low", name: "B", cpu: 0, memory: 10 }, { id: "high", name: "C", cpu: 50, memory: 20 }];
  assert.deepEqual(sortTaskRows(rows, "cpu").map(row => row.id), ["high", "low", "tab"]);
  assert.deepEqual(sortTaskRows(rows, "ram").map(row => row.id), ["high", "low", "tab"]);
  assert.deepEqual(sortTaskRows(rows, "name").map(row => row.id), ["tab", "low", "high"]);
  assert.equal(rows[0].id, "tab");
  assert.deepEqual(flattenProcesses(null), []);
});

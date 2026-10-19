/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. See https://mozilla.org/MPL/2.0/. */

/**
 * Privacy Browser's process inspector. All statistics are PROCESS statistics:
 * Gecko can share one process between tabs, extensions and subframes. A tab
 * row deliberately has no invented per-tab CPU or memory number.
 *
 * This module has no privileged imports at module evaluation time so its
 * resource accounting and protection rules can be tested independently.
 */
export const TASK_PAGE = "chrome://privacybrowser/content/tasks.xhtml";
const windowState = new WeakMap();
const tabIds = new WeakMap();
let nextTabId = 1;

function services() {
  return Services;
}

export function flattenProcesses(snapshot) {
  if (!snapshot || !Number.isFinite(Number(snapshot.pid))) {
    return [];
  }
  return [snapshot, ...(snapshot.children || []).flatMap(flattenProcesses)];
}

/** ChromeUtils.requestProcInfo reports cumulative CPU nanoseconds. */
export function cpuPercent(current, previous, elapsedMs) {
  if (!previous || !Number.isFinite(elapsedMs) || elapsedMs <= 0) {
    return null;
  }
  const delta = Number(current.cpuTime) - Number(previous.cpuTime);
  if (!Number.isFinite(delta) || delta < 0) {
    return null; // PID reuse, a counter reset, or unavailable sample.
  }
  return (delta / (elapsedMs * 1_000_000)) * 100;
}

export function isProtectedProcess(type) {
  // Process termination is never exposed by this page, including web process
  // termination. This classification additionally prevents closing chrome tabs.
  return !["web", "webIsolated", "withCoopCoep", "file"].includes(type);
}

export function buildTaskRows({ snapshot, previousSnapshot, elapsedMs, tabs = [] }) {
  const previous = new Map(flattenProcesses(previousSnapshot).map(p => [Number(p.pid), p]));
  const rows = [];
  for (const process of flattenProcesses(snapshot)) {
    const pid = Number(process.pid);
    const matches = tabs.filter(tab => Number(tab.pid) === pid);
    const type = process.type || "unknown";
    const protectedProcess = isProtectedProcess(type);
    rows.push({
      id: `process-${pid}`,
      kind: "process",
      name: type === "browser" ? "Privacy Browser" : `${type} process`,
      pid,
      cpu: cpuPercent(process, previous.get(pid), elapsedMs),
      memory: Number.isFinite(Number(process.memory)) ? Number(process.memory) : null,
      type,
      protected: protectedProcess,
      state: protectedProcess ? "Protected internal process" :
        matches.length > 1 ? `Shared by ${matches.length} tabs; may also host subframes` :
          "Process total; may also host subframes",
    });
    for (const tab of matches) {
      rows.push({
        id: tab.id,
        kind: "tab",
        name: tab.name || "Untitled tab",
        pid,
        cpu: null,
        memory: null,
        type: "tab",
        protected: Boolean(tab.protected),
        state: tab.pending ? "Suspended" : tab.busy ? "Loading" :
          matches.length > 1 ? `Running; shared PID (${matches.length} tabs)` : "Running; see process total",
      });
    }
  }
  // Pending/discarded tabs do not necessarily have a live content PID.
  const included = new Set(rows.map(row => row.id));
  for (const tab of tabs.filter(tab => !included.has(tab.id))) {
    rows.push({ id: tab.id, kind: "tab", name: tab.name || "Untitled tab",
      pid: tab.pid || null, cpu: null, memory: null, type: "tab",
      protected: Boolean(tab.protected), state: tab.pending ? "Suspended" : "No process sample" });
  }
  return rows;
}

export function sortTaskRows(rows, field = "cpu") {
  const key = field === "ram" ? "memory" : field;
  return [...rows].sort((a, b) => {
    if (key === "name") {
      return a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
    }
    const left = a[key], right = b[key];
    if (left == null && right != null) return 1;
    if (right == null && left != null) return -1;
    return (right ?? -1) - (left ?? -1) || a.id.localeCompare(b.id);
  });
}

function collectTabs() {
  const tabs = [], liveTabs = new Map();
  for (const win of services().wm.getEnumerator("navigator:browser")) {
    for (const tab of win.gBrowser?.tabs || []) {
      if (!tabIds.has(tab)) tabIds.set(tab, `tab-${nextTabId++}`);
      const id = tabIds.get(tab);
      const browser = tab.linkedBrowser;
      const uri = browser?.currentURI?.spec || "";
      const scheme = uri.split(":", 1)[0];
      const internal = ["chrome", "resource", "about"].includes(scheme);
      tabs.push({ id, name: tab.label, pid: browser?.browsingContext?.currentWindowGlobal?.osPid ||
          browser?.frameLoader?.remoteTab?.osPid || null,
        pending: tab.hasAttribute("pending"), busy: tab.hasAttribute("busy"),
        protected: internal });
      liveTabs.set(id, { win, tab });
    }
  }
  return { tabs, liveTabs };
}

function displayBytes(bytes) {
  return bytes == null ? "—" : `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}

function attachTaskPage(dialog, sourceWindow, selectedTab) {
  const doc = dialog.document;
  const body = doc.getElementById("task-rows");
  if (!body) return;
  const status = doc.getElementById("task-status");
  const sort = doc.getElementById("task-sort");
  const close = doc.getElementById("task-close");
  const force = doc.getElementById("task-kill");
  let selectedId = selectedTab && tabIds.get(selectedTab);
  let rows = [], liveTabs = new Map(), previousSnapshot = null, previousTime = null;
  let refreshing = false, stopped = false;

  const selected = () => rows.find(row => row.id === selectedId);
  const updateButtons = () => {
    const row = selected();
    const allowed = row?.kind === "tab" && !row.protected;
    if (close) close.disabled = !allowed;
    if (force) force.disabled = !allowed;
  };
  function render() {
    body.replaceChildren();
    for (const row of sortTaskRows(rows, sort?.value || "cpu")) {
      const tr = doc.createElement("tr");
      tr.dataset.taskId = row.id;
      tr.setAttribute("tabindex", "0");
      tr.setAttribute("aria-selected", String(row.id === selectedId));
      if (row.id === selectedId) tr.classList.add("selected");
      for (const value of [row.name, row.pid ?? "—", row.cpu == null ? "—" : `${row.cpu.toFixed(1)}%`,
        displayBytes(row.memory), row.type, row.state]) {
        const td = doc.createElement("td");
        td.textContent = String(value); // Page-controlled titles never become HTML.
        tr.append(td);
      }
      const select = () => { selectedId = row.id; render(); };
      tr.addEventListener("click", select);
      tr.addEventListener("keydown", event => {
        if (["Enter", " "].includes(event.key)) { event.preventDefault(); select(); }
      });
      body.append(tr);
    }
    updateButtons();
  }
  async function refresh() {
    if (refreshing || stopped || dialog.closed || doc.hidden) return;
    refreshing = true;
    try {
      const snapshot = await ChromeUtils.requestProcInfo();
      if (stopped || dialog.closed) return;
      const now = dialog.performance.now();
      const collected = collectTabs();
      liveTabs = collected.liveTabs;
      if (!selectedId && selectedTab) selectedId = tabIds.get(selectedTab);
      rows = buildTaskRows({ snapshot, previousSnapshot,
        elapsedMs: previousTime == null ? null : now - previousTime, tabs: collected.tabs });
      previousSnapshot = snapshot;
      previousTime = now;
      render();
      if (status) status.textContent = "CPU and RAM are process totals. Shared tab costs cannot be separated. Extension processes are included; use Firefox Process Manager for extension details.";
    } catch {
      if (status) status.textContent = "Process data unavailable. Open about:processes for the built-in inspector.";
    } finally { refreshing = false; }
  }
  async function closeSelected(forceClose) {
    const row = selected();
    if (row?.kind !== "tab" || row.protected) return;
    const target = liveTabs.get(row.id);
    if (!target || target.win.closed || !target.tab.isConnected) return;
    const message = forceClose ?
      "Force close the selected tab? Unsaved input can be lost. Shared browser processes will not be killed." :
      "Close the selected tab?";
    if (!services().prompt.confirm(dialog, "Privacy Browser Task Manager", message)) return;
    // Re-check the tab identity after a nested event loop in the prompt.
    if (!target.tab.isConnected || target.win.closed) return;
    target.win.gBrowser.removeTab(target.tab, { animate: false, skipPermitUnload: forceClose });
    selectedId = null;
    await refresh();
  }
  doc.getElementById("task-refresh")?.addEventListener("click", refresh);
  sort?.addEventListener("change", render);
  close?.addEventListener("click", () => closeSelected(false));
  force?.addEventListener("click", () => closeSelected(true));
  doc.getElementById("task-native")?.addEventListener("click", () => {
    sourceWindow.openTrustedLinkIn("about:processes", "tab");
  });
  const timer = dialog.setInterval(refresh, 2000);
  dialog.addEventListener("unload", () => { stopped = true; dialog.clearInterval(timer); }, { once: true });
  doc.addEventListener("visibilitychange", refresh);
  refresh();
}

export function initTaskManager(win) {
  // UI owns menu and accelerator registration. Idempotent registration here
  // ensures a single dialog per source browser window.
  if (!windowState.has(win)) windowState.set(win, { dialog: null });
}

export function openTaskManager(win, selectedTab = null) {
  initTaskManager(win);
  const state = windowState.get(win);
  if (state.dialog && !state.dialog.closed) {
    // Reopen when a different context tab is requested, so its selection is exact.
    if (!selectedTab) { state.dialog.focus(); return state.dialog; }
    state.dialog.close();
  }
  // Collect IDs before opening the new chrome window.
  collectTabs();
  const dialog = win.openDialog(TASK_PAGE, "privacybrowser-tasks",
    "chrome,dialog=no,resizable,width=1000,height=650");
  state.dialog = dialog;
  dialog.addEventListener("load", () => attachTaskPage(dialog, win, selectedTab), { once: true });
  return dialog;
}

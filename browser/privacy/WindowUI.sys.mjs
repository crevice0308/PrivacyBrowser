import { openTaskManager } from "resource://privacybrowser/TaskManager.sys.mjs";

const HOME = "chrome://privacybrowser/content/home.html";
const SETTINGS = "chrome://privacybrowser/content/settings.html";
const initialized = new WeakMap();

/** Only trusted browser-window callers reach this module. No content message bridge. */
function openPage(win, url, { focusSearch = false } = {}) {
  const browser = win.gBrowser;
  let tab = [...browser.tabs].find(candidate => candidate.linkedBrowser.currentURI?.spec.split("#")[0] === url);
  if (!tab) tab = browser.addTrustedTab(url, { inBackground: false });
  browser.selectedTab = tab;
  if (focusSearch) {
    const focus = () => {
      try {
        const doc = tab.linkedBrowser.contentDocument;
        if (doc?.nodePrincipal?.isSystemPrincipal) doc.getElementById("search-input")?.focus();
      } catch { /* A new chrome document focuses its own search input on load. */ }
    };
    focus();
    tab.linkedBrowser.addEventListener("load", focus, { once: true, capture: true });
  }
  return tab;
}

export function openSearch(win) {
  return openPage(win, HOME, { focusSearch: true });
}
export function openSettings(win) {
  return openPage(win, SETTINGS);
}

function xul(doc, name, attributes = {}) {
  const node = doc.createXULElement(name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}

/** Add a small branded menu without replacing Firefox's security or navigation UI. */
export function initWindowUI(win) {
  if (initialized.has(win)) return;
  const doc = win.document;
  const cleanup = [];
  const link = doc.createElementNS("http://www.w3.org/1999/xhtml", "link");
  link.rel = "stylesheet";
  link.href = "chrome://privacybrowser/skin/browser.css";
  doc.documentElement.append(link);
  cleanup.push(() => link.remove());

  const palette = doc.getElementById("nav-bar-customization-target") || doc.getElementById("nav-bar");
  if (palette) {
    const searchButton = xul(doc, "toolbarbutton", {
      id: "privacybrowser-search-button", class: "toolbarbutton-1 chromeclass-toolbar-additional",
      label: "Search", tooltiptext: "Search the web (Ctrl+K)",
    });
    searchButton.addEventListener("command", () => openSearch(win));
    const menuButton = xul(doc, "toolbarbutton", {
      id: "privacybrowser-menu-button", class: "toolbarbutton-1 chromeclass-toolbar-additional",
      type: "menu", label: "Privacy Browser", tooltiptext: "Privacy Browser controls",
    });
    const menu = xul(doc, "menupopup", { id: "privacybrowser-menu-popup" });
    for (const [label, handler] of [
      ["Search the web", () => openSearch(win)],
      ["Privacy settings", () => openSettings(win)],
      ["Task Manager", () => openTaskManager(win, win.gBrowser.selectedTab)],
      ["Downloads", () => win.BrowserDownloadsUI()],
      ["Bookmarks", () => win.PlacesCommandHook.showPlacesOrganizer("AllBookmarks")],
      ["Add-ons", () => win.BrowserAddonUI()],
    ]) {
      const item = xul(doc, "menuitem", { label });
      item.addEventListener("command", handler);
      menu.append(item);
    }
    menuButton.append(menu);
    const before = doc.getElementById("PanelUI-menu-button");
    if (before?.parentNode === palette) {
      palette.insertBefore(searchButton, before);
      palette.insertBefore(menuButton, before);
    } else {
      palette.append(searchButton, menuButton);
    }
    cleanup.push(() => { searchButton.remove(); menuButton.remove(); });
  }

  const context = doc.getElementById("tabContextMenu");
  if (context) {
    const item = xul(doc, "menuitem", { id: "privacybrowser-context-task-manager", label: "Open in Task Manager" });
    item.addEventListener("command", () => openTaskManager(win, win.TabContextMenu.contextTab || win.gBrowser.selectedTab));
    context.append(item);
    cleanup.push(() => item.remove());
  }

  const tools = doc.getElementById("menu_ToolsPopup");
  if (tools) {
    const item = xul(doc, "menuitem", { id: "privacybrowser-tools-task-manager", label: "Privacy Browser Task Manager", accesskey: "T" });
    item.addEventListener("command", () => openTaskManager(win, win.gBrowser.selectedTab));
    tools.append(item);
    cleanup.push(() => item.remove());
  }

  const onKey = event => {
    if (event.defaultPrevented || event.repeat || event.altKey || event.metaKey) return;
    if (event.ctrlKey && !event.shiftKey && event.key.toLowerCase() === "k") {
      event.preventDefault();
      event.stopImmediatePropagation();
      openSearch(win);
    } else if (event.shiftKey && !event.ctrlKey && event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      openTaskManager(win, win.gBrowser.selectedTab);
    } else if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "t" && !Services.prefs.getBoolPref("privacybrowser.history.sessions", false)) {
      // No restored closed-tab data is exposed in the nonpersistent mode.
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  };
  win.addEventListener("keydown", onKey, true);
  cleanup.push(() => win.removeEventListener("keydown", onKey, true));
  initialized.set(win, cleanup);
  win.addEventListener("unload", () => uninitWindowUI(win), { once: true });
}

export function uninitWindowUI(win) {
  for (const callback of initialized.get(win) || []) {
    try { callback(); } catch { /* The browser window can already be closing. */ }
  }
  initialized.delete(win);
}

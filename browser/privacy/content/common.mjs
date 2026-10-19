/* These modules are chrome-only. Never register the package contentaccessible. */


if (!document.nodePrincipal?.isSystemPrincipal) {
  throw new Error("Privacy Browser controls require a system-principal document.");
}

const { BrowserServices: Services } = ChromeUtils.importESModule("resource://privacybrowser/BrowserServices.sys.mjs");
export { Services };
export const PREF_ROOT = "privacybrowser.";
export function booleanPreference(name, fallback = false) {
  return Services.prefs.getBoolPref(PREF_ROOT + name, fallback);
}
export function stringPreference(name, fallback = "") {
  return Services.prefs.getStringPref(PREF_ROOT + name, fallback);
}
export function requestAction(topic) {
  if (!/^privacybrowser:(?:clear-data|diagnostics-export|diagnostics-delete|diagnostics-choose-directory)$/u.test(topic)) {
    throw new Error("Unknown browser control action.");
  }
  Services.obs.notifyObservers(null, topic);
}
export function showStatus(message) {
  const status = document.getElementById("status");
  if (status) status.textContent = message;
}

/** Let the browser chrome perform process switching for a trusted local page. */
export function navigateTrusted(url) {
  if (!document.nodePrincipal.isSystemPrincipal) throw new Error("Untrusted browser page");
  let owner, tab;
  for (const candidate of Services.wm.getEnumerator("navigator:browser")) {
    const found = [...candidate.gBrowser.tabs].find(item => item.linkedBrowser.contentDocument === document);
    if (found) { owner = candidate; tab = found; break; }
  }
  if (!tab) throw new Error("No trusted browser host");
  const uri = Services.io.newURI(url);
  if (uri.scheme !== "https" && !["about:preferences#search", "about:preferences#privacy", "about:support"].includes(url)) {
    throw new Error("Unsupported browser navigation");
  }
  owner.gBrowser.selectedTab = tab;
  owner.openTrustedLinkIn(url, "current");
}

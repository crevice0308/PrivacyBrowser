import { Services, PREF_ROOT, booleanPreference, stringPreference, requestAction, showStatus, navigateTrusted } from "./common.mjs";
import { SEARCH_ENGINES, validateSearchTemplate } from "./search-logic.mjs";

for (const checkbox of document.querySelectorAll("[data-pref]")) {
  const name = checkbox.dataset.pref;
  checkbox.checked = booleanPreference(name, checkbox.dataset.default === "true");
  checkbox.addEventListener("change", () => {
    Services.prefs.setBoolPref(PREF_ROOT + name, checkbox.checked);
    Services.prefs.savePrefFile(null);
    showStatus(name.startsWith("history.") ? "Saved. Restart Privacy Browser to apply the privacy mode consistently." : "Saved.");
  });
}

const engine = document.getElementById("search-engine");
const customFields = document.getElementById("custom-engine-fields");
const customName = document.getElementById("custom-engine-name");
const customURL = document.getElementById("custom-engine-url");
const currentTemplate = stringPreference("search.template", SEARCH_ENGINES[0].template);
const engineIndex = SEARCH_ENGINES.findIndex(item => item.template === currentTemplate);
engine.value = engineIndex === -1 ? "custom" : String(engineIndex);
customFields.hidden = engine.value !== "custom";
customName.value = stringPreference("search.name", "Brave Search");
customURL.value = currentTemplate;

function saveEngine(name, template) {
  try {
    validateSearchTemplate(template);
    if (!name.trim()) throw new Error("Enter a name for your search engine.");
    Services.prefs.setStringPref(PREF_ROOT + "search.name", name.trim().slice(0, 80));
    Services.prefs.setStringPref(PREF_ROOT + "search.template", template);
    Services.prefs.savePrefFile(null);
    showStatus("Search engine saved. New searches use your choice.");
  } catch (error) {
    showStatus(error.message);
  }
}
engine.addEventListener("change", () => {
  customFields.hidden = engine.value !== "custom";
  if (!customFields.hidden) {
    customName.focus();
    return;
  }
  const selected = SEARCH_ENGINES[Number(engine.value)];
  saveEngine(selected.name, selected.template);
});
document.getElementById("save-search-engine").addEventListener("click", () => saveEngine(customName.value, customURL.value.trim()));

function wireInteger(id, preference, fallback, min, max) {
  const element = document.getElementById(id);
  element.value = Services.prefs.getIntPref(PREF_ROOT + preference, fallback);
  element.addEventListener("change", () => {
    const value = Number(element.value);
    if (!Number.isInteger(value) || value < min || value > max) {
      showStatus(`Enter a whole number between ${min} and ${max}.`);
      element.value = Services.prefs.getIntPref(PREF_ROOT + preference, fallback);
      return;
    }
    Services.prefs.setIntPref(PREF_ROOT + preference, value);
    Services.prefs.savePrefFile(null);
    showStatus("Saved.");
  });
}
wireInteger("suspend-minutes", "tabs.suspend.minutes", 15, 1, 1440);
wireInteger("diagnostic-size", "diagnostics.maxBytes", 1048576, 65536, 10485760);
wireInteger("diagnostic-files", "diagnostics.maxFiles", 3, 1, 20);
const level = document.getElementById("diagnostic-level");
level.value = stringPreference("diagnostics.level", "warn");
level.addEventListener("change", () => {
  Services.prefs.setStringPref(PREF_ROOT + "diagnostics.level", level.value);
  Services.prefs.savePrefFile(null);
  showStatus("Diagnostic level saved.");
});

const directoryLabel = document.getElementById("diagnostic-directory");
function refreshDirectory() {
  directoryLabel.textContent = stringPreference("diagnostics.directory") || "Browser profile diagnostics folder";
}
refreshDirectory();
const directoryObserver = { observe: refreshDirectory };
Services.prefs.addObserver(PREF_ROOT + "diagnostics.directory", directoryObserver);
window.addEventListener("unload", () => Services.prefs.removeObserver(PREF_ROOT + "diagnostics.directory", directoryObserver), { once: true });

document.getElementById("clear-data").addEventListener("click", () => {
  if (window.confirm("Clear existing browser history, cookies, cache, session data, and site storage? Bookmarks and downloaded files are kept. Website sign-ins will end.")) {
    requestAction("privacybrowser:clear-data");
    showStatus("Clear request sent. Close open website tabs and restart after cleanup.");
  }
});
document.getElementById("choose-diagnostic-directory").addEventListener("click", () => requestAction("privacybrowser:diagnostics-choose-directory"));
document.getElementById("export-diagnostics").addEventListener("click", () => requestAction("privacybrowser:diagnostics-export"));
document.getElementById("delete-diagnostics").addEventListener("click", () => {
  if (window.confirm("Delete the diagnostic log files? Exported diagnostic ZIP files are kept.")) requestAction("privacybrowser:diagnostics-delete");
});
document.getElementById("native-search-settings").addEventListener("click", () => navigateTrusted("about:preferences#search"));
document.getElementById("native-permission-settings").addEventListener("click", () => navigateTrusted("about:preferences#privacy"));
document.getElementById("native-about").addEventListener("click", () => navigateTrusted("about:support"));

document.documentElement.dataset.privacyBrowserReady = "true";

import { Services, booleanPreference, stringPreference, showStatus, navigateTrusted } from "./common.mjs";
import { buildSearchURL, DEFAULT_SEARCH_TEMPLATE } from "./search-logic.mjs";

const input = document.getElementById("search-input");
const name = stringPreference("search.name", "Brave Search");
input.placeholder = `Search with ${name}`;
document.getElementById("engine-name").textContent = name;
const flags = ["browsing", "search", "cookies", "downloads", "forms", "cache", "sessions", "siteStorage"];
document.getElementById("history-label").textContent = flags.some(flag => booleanPreference(`history.${flag}`)) ? "Custom privacy settings" : "Zero History";
document.getElementById("search-form").addEventListener("submit", event => {
  event.preventDefault();
  try {
    const url = buildSearchURL(input.value, stringPreference("search.template", DEFAULT_SEARCH_TEMPLATE));
    if (!url) {
      input.focus();
      return;
    }
    // The privacy backend may retain this only when search history is enabled.
    // Diagnostic code must never subscribe to or serialize this topic's data.
    Services.obs.notifyObservers(null, "privacybrowser:search-performed", input.value.trim());
    navigateTrusted(url);
    document.documentElement.dataset.searchDispatched = "true";
  } catch {
    showStatus("Check the search engine URL in Settings.");
  }
});
input.focus();

document.documentElement.dataset.privacyBrowserReady = "true";

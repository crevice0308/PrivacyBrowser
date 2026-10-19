/** Pure helpers, shared by chrome UI and Node tests. Never records a query. */
export const DEFAULT_SEARCH_TEMPLATE = "https://search.brave.com/search?q={searchTerms}";
export const SEARCH_ENGINES = Object.freeze([
  { name: "Brave Search", template: DEFAULT_SEARCH_TEMPLATE },
  { name: "DuckDuckGo", template: "https://duckduckgo.com/?q={searchTerms}" },
  { name: "Google", template: "https://www.google.com/search?q={searchTerms}" },
  { name: "Bing", template: "https://www.bing.com/search?q={searchTerms}" },
]);

export function validateSearchTemplate(template) {
  if (typeof template !== "string" || template.split("{searchTerms}").length !== 2) {
    throw new TypeError("The search URL must contain {searchTerms} exactly once.");
  }
  // Parse the substituted URL, so a placeholder in the hostname cannot be used.
  const marker = "privacybrowser-template-check";
  const url = new URL(template.replace("{searchTerms}", marker));
  if (url.protocol !== "https:" || url.username || url.password || url.hostname.includes(marker)) {
    throw new TypeError("Use an HTTPS search URL without embedded credentials.");
  }
  return template;
}

export function buildSearchURL(query, template = DEFAULT_SEARCH_TEMPLATE) {
  if (typeof query !== "string") {
    throw new TypeError("Search text must be a string.");
  }
  const text = query.trim();
  if (!text) return null;
  validateSearchTemplate(template);
  return template.replace("{searchTerms}", encodeURIComponent(text));
}

/** URL-only input feedback. Gecko remains responsible for navigation security. */
export function isAddressLike(input) {
  if (typeof input !== "string") return false;
  const text = input.trim();
  if (!text || /\s/u.test(text)) return false;
  if (/^(?:about|file):/iu.test(text)) return true;
  try {
    const url = new URL(/^[a-z][a-z\d+.-]*:\/\//iu.test(text) ? text : `https://${text}`);
    if (!new Set(["https:", "http:"]).has(url.protocol) || url.username || url.password) return false;
    return url.hostname === "localhost" || url.hostname.includes(".") || url.hostname.startsWith("[");
  } catch {
    return false;
  }
}

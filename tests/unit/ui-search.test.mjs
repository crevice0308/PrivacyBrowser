import test from "node:test";
import assert from "node:assert/strict";
import { buildSearchURL, validateSearchTemplate, isAddressLike, SEARCH_ENGINES } from "../../browser/privacy/content/search-logic.mjs";

for (const query of ["Privacy Browser", "Ubuntu Linux", "japan no1", "あいうえお", "日本語テスト", "Firefox ESR"]) {
  test(`dedicated search preserves Unicode: ${query}`, () => {
    const url = new URL(buildSearchURL(query));
    assert.equal(url.origin, "https://search.brave.com");
    assert.equal(url.searchParams.get("q"), query);
  });
}
test("search escapes separators and script text as data", () => {
  const text = "hello &q=changed#fragment <script>";
  const url = new URL(buildSearchURL(text));
  assert.equal(url.searchParams.size, 1);
  assert.equal(url.searchParams.get("q"), text);
  assert.equal(url.hash, "");
});
test("blank searches never navigate", () => {
  assert.equal(buildSearchURL("\t \u3000"), null);
});
test("all bundled providers accept queries", () => {
  for (const engine of SEARCH_ENGINES) assert.ok(buildSearchURL("日本語", engine.template).startsWith("https://"));
});
test("custom provider validates scheme, credentials, placeholder count and host", () => {
  for (const template of ["javascript:alert('{searchTerms}')", "http://example.com/?q={searchTerms}", "https://example.com", "https://example.com/?q={searchTerms}&a={searchTerms}", "https://u:p@example.com/?q={searchTerms}", "https://{searchTerms}.example.com/"]) {
    assert.throws(() => validateSearchTemplate(template));
  }
  assert.equal(buildSearchURL("あいう", "https://example.com/find/{searchTerms}"), "https://example.com/find/%E3%81%82%E3%81%84%E3%81%86");
});
test("URL feedback distinguishes phrases from addresses without triggering search", () => {
  for (const input of ["https://example.org/path?q=hello", "example.org", "localhost:8080", "127.0.0.1", "https://[::1]/", "about:preferences", "file:///tmp/example.html"]) assert.equal(isAddressLike(input), true, input);
  for (const input of ["Privacy Browser", "日本語テスト", "two words", "javascript:alert(1)", "https://u:p@example.org", ""]) assert.equal(isAddressLike(input), false, input);
});

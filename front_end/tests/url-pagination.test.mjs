import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

function harness(initialUrl) {
  const browser = {
    location: new URL(initialUrl),
    replacements: [],
    history: {
      replaceState(state, title, url) {
        browser.replacements.push({ state, title, url: String(url) });
        browser.location = new URL(url, browser.location);
      },
    },
  };
  const source = readFileSync(new URL("../src/hooks/use-url-pagination.ts", import.meta.url), "utf8");
  const code = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", "window", code)(
    name => {
      if (name === "react") return { useCallback: callback => callback };
      if (name === "next/navigation") return { useSearchParams: () => browser.location.searchParams };
      throw new Error(`Unexpected module: ${name}`);
    }, module, module.exports, browser,
  );
  return { browser, pagination: module.exports.useUrlPagination };
}

test("page changes write the URL immediately without a route request and retain filters and hash", () => {
  const h = harness("https://example.test/cluster?owner_id=alice#running");
  h.pagination({ prefix: "running", defaultPageSize: 5 }).setPage(5);
  assert.equal(h.browser.location.searchParams.get("running_page"), "5");
  assert.equal(h.pagination({ prefix: "running", defaultPageSize: 5 }).page, 5);
  assert.equal(h.browser.location.searchParams.get("owner_id"), "alice");
  assert.equal(h.browser.location.hash, "#running");
  assert.equal(h.browser.replacements[0].state, null, "Next must handle the native history update, not bypass it as an internal navigation");
});

test("same-tick sibling pagination and filter changes merge against the live URL", () => {
  const h = harness("https://example.test/cluster");
  const running = h.pagination({ prefix: "running", defaultPageSize: 5 });
  const pending = h.pagination({ prefix: "pending", defaultPageSize: 5 });
  running.setPage(5);
  pending.setPage(3);
  running.setParams({ owner_id: "alice" });
  assert.deepEqual(Object.fromEntries(h.browser.location.searchParams), { running_page: "5", pending_page: "3", owner_id: "alice" });
});

test("page size resets only its own page, removes defaults, and preserves URL restoration", () => {
  const h = harness("https://example.test/cluster?running_page=5&pending_page=3");
  const running = h.pagination({ prefix: "running", defaultPageSize: 5 });
  running.setPageSize(20);
  assert.deepEqual(Object.fromEntries(h.browser.location.searchParams), { pending_page: "3", running_page_size: "20" });
  assert.equal(h.pagination({ prefix: "running", defaultPageSize: 5 }).pageSize, 20);
  running.setPageSize(5);
  assert.deepEqual(Object.fromEntries(h.browser.location.searchParams), { pending_page: "3" });
  h.browser.location = new URL("https://example.test/cluster?running_page=7&running_page_size=10");
  assert.equal(h.pagination({ prefix: "running", defaultPageSize: 5 }).page, 7);
  assert.equal(h.pagination({ prefix: "running", defaultPageSize: 5 }).pageSize, 10);
});

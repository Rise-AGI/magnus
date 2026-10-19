import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

// Run the page's actual polling callback without Next or a browser.
const text = readFileSync(new URL("../src/app/(main)/cluster/page.tsx", import.meta.url), "utf8");
const source = ts.createSourceFile("cluster-page.tsx", text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback;
function findFetch(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(source) === "fetchData") {
    assert.ok(node.initializer && ts.isCallExpression(node.initializer));
    callback = node.initializer.arguments[0].getText(source);
  }
  ts.forEachChild(node, findFetch);
}
findFetch(source);
assert.ok(callback, "Production cluster fetch callback must exist");
const compiled = ts.transpileModule(`
export function createFetchData(bindings: any) {
  const { client, console, fetchRequest, myPage, mySize, runningPage, runningSize,
    pendingPage, pendingSize, setLoading, setClusterRefreshFailed, setActiveJobs,
    setTotalMyJobs, setCluster } = bindings;
  return (${callback});
}`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { createFetchData } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

function harness() {
  const calls = [];
  const state = { loading: true, cluster: null, stale: false, activeJobs: [], totalMyJobs: 0 };
  const bindings = {
    console: { warn() {}, error() {} },
    fetchRequest: { current: null },
    mySize: 5, runningSize: 5, pendingSize: 5, runningPage: 1, pendingPage: 1,
    client(url) {
      return new Promise((resolve, reject) => calls.push({ url, resolve, reject }));
    },
    setLoading(value) { state.loading = value; },
    setClusterRefreshFailed(value) { state.stale = value; },
    setActiveJobs(value) { state.activeJobs = value; },
    setTotalMyJobs(value) { state.totalMyJobs = value; },
    setCluster(value) { state.cluster = value; },
  };
  function resolvePair(offset, name) {
    calls[offset].resolve({ items: [{ id: name, status: "Pending" }], total: 1 });
    calls[offset + 1].resolve({ name, resources: { is_stale: false }, running_jobs: [] });
  }
  return { calls, state, resolvePair, page: myPage => createFetchData({ ...bindings, myPage }) };
}

test("slow cluster fetch survives multiple polling ticks and releases the next poll", async () => {
  const h = harness();
  const fetch = h.page(1);
  const first = fetch();
  for (let tick = 0; tick < 5; tick++) await fetch(true);
  assert.equal(h.calls.length, 2, "Polling must not start overlapping requests for the same page");
  assert.equal(h.state.loading, true);
  h.resolvePair(0, "slow-first-result");
  await first;
  assert.equal(h.state.cluster.name, "slow-first-result");
  assert.equal(h.state.loading, false);
  const next = fetch(true);
  assert.equal(h.calls.length, 4);
  h.resolvePair(2, "next-result");
  await next;
  assert.equal(h.state.cluster.name, "next-result");
});

test("pagination invalidates the previous generation without invalidating same-page polls", async () => {
  const h = harness();
  const old = h.page(1)();
  const newPage = h.page(2);
  const current = newPage();
  await newPage(true);
  assert.equal(h.calls.length, 4);
  assert.match(h.calls[2].url, /skip=5/);
  h.resolvePair(0, "old-page");
  await old;
  assert.equal(h.state.cluster, null);
  assert.equal(h.state.loading, true, "Old completion must not end the current page's loading state");
  h.resolvePair(2, "new-page");
  await current;
  assert.equal(h.state.cluster.name, "new-page");
  assert.equal(h.state.activeJobs[0].id, "new-page");
  assert.equal(h.state.loading, false);
});

test("old-generation errors cannot mark a newer successful snapshot stale", async () => {
  const h = harness();
  const old = h.page(1)();
  const current = h.page(2)();
  h.resolvePair(2, "new-page");
  await current;
  h.calls[0].reject(new Error("old active jobs failed"));
  h.calls[1].reject(new Error("old cluster failed"));
  await old;
  assert.equal(h.state.cluster.name, "new-page");
  assert.equal(h.state.stale, false);
});

test("failed refresh preserves prior data, marks it stale, and permits recovery", async () => {
  const h = harness();
  const fetch = h.page(1);
  const initial = fetch();
  h.resolvePair(0, "last-good");
  await initial;
  const failed = fetch(true);
  h.calls[2].resolve({ items: [], total: 0 });
  h.calls[3].reject(new Error("scheduler timed out"));
  await failed;
  assert.equal(h.state.cluster.name, "last-good");
  assert.equal(h.state.stale, true);
  assert.equal(h.state.loading, false);
  const recovery = fetch(true);
  h.resolvePair(4, "recovered");
  await recovery;
  assert.equal(h.state.cluster.name, "recovered");
  assert.equal(h.state.stale, false);
});

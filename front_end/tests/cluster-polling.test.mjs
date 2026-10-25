import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

// Run the page's actual polling callback without Next or a browser.
const text = readFileSync(new URL("../src/app/(main)/cluster/page.tsx", import.meta.url), "utf8");
const source = ts.createSourceFile("cluster-page.tsx", text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback;
const pageDeclarations = new Map();
const tableProps = [];
function findFetch(node) {
  if (ts.isVariableDeclaration(node)) pageDeclarations.set(node.name.getText(source), node.getText(source));
  if (ts.isVariableDeclaration(node) && node.name.getText(source) === "fetchData") {
    assert.ok(node.initializer && ts.isCallExpression(node.initializer));
    callback = node.initializer.arguments[0].getText(source);
  }
  if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(source) === "JobTable") {
    tableProps.push(node.attributes.properties.filter(prop => ["jobs", "loading", "emptyMessage"].includes(prop.name?.getText(source)))
      .map(prop => `${prop.name.getText(source)}: (${prop.initializer.expression.getText(source)})`).join(","));
  }
  ts.forEachChild(node, findFetch);
}
findFetch(source);
assert.ok(callback, "Production cluster fetch callback must exist");
const compiled = ts.transpileModule(`
export function createFetchData(bindings: any) {
  const { client, console, fetchRequest, myPage, mySize, runningPage, runningSize,
    pendingPage, pendingSize, setLoading, setClusterRefreshFailed, setActiveJobs,
    setTotalMyJobs, setCluster, setLoadedPages, setSettledRequestKey } = bindings;
  ${["myKey", "runningKey", "pendingKey", "requestKey"].map(name => `const ${pageDeclarations.get(name)};`).join("\n")}
  return (${callback});
}
export function pageView(bindings: any) {
  const { myPage, mySize, runningPage, runningSize, pendingPage, pendingSize,
    loadedPages, settledRequestKey, loading, activeJobs, cluster } = bindings;
  const t = (key: string) => key;
  ${["myKey", "runningKey", "pendingKey", "requestKey", "waitingForPage", "myPageReady", "runningPageReady", "pendingPageReady"].map(name => `const ${pageDeclarations.get(name)};`).join("\n")}
  return [${tableProps.map(props => `({${props}})`).join(",")}];
}`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { createFetchData, pageView } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

function harness() {
  const calls = [];
  const state = { loading: true, cluster: null, stale: false, activeJobs: [], totalMyJobs: 0,
    loadedPages: { my: "", running: "", pending: "" }, settledRequestKey: "" };
  const bindings = {
    console: { warn() {}, error() {} },
    fetchRequest: { current: null },
    myPage: 1, mySize: 5, runningSize: 5, pendingSize: 5, runningPage: 1, pendingPage: 1,
    client(url) {
      return new Promise((resolve, reject) => calls.push({ url, resolve, reject }));
    },
    setLoading(value) { state.loading = value; },
    setClusterRefreshFailed(value) { state.stale = value; },
    setActiveJobs(value) { state.activeJobs = value; },
    setTotalMyJobs(value) { state.totalMyJobs = value; },
    setCluster(value) { state.cluster = value; },
    setLoadedPages(update) { state.loadedPages = update(state.loadedPages); },
    setSettledRequestKey(value) { state.settledRequestKey = value; },
  };
  function resolvePair(offset, name) {
    calls[offset].resolve({ items: [{ id: name, status: "Pending" }], total: 1 });
    calls[offset + 1].resolve({ name, resources: { is_stale: false }, running_jobs: [{ id: name }], pending_jobs: [{ id: name }] });
  }
  return { calls, state, resolvePair, page: myPage => createFetchData({ ...bindings, myPage }),
    running: runningPage => createFetchData({ ...bindings, runningPage }),
    view: (pages = {}) => pageView({ ...bindings, ...state, ...pages }) };
}

test("changing pages immediately replaces old rows with a loader only in that panel", async () => {
  const h = harness();
  const first = h.running(1)();
  h.resolvePair(0, "page-1");
  await first;
  const beforeEffect = h.view({ runningPage: 5 });
  assert.equal(beforeEffect[0].loading, false);
  assert.equal(beforeEffect[1].loading, true, "Feedback must not wait for the request effect");
  assert.deepEqual(beforeEffect[1].jobs, []);
  assert.equal(beforeEffect[2].loading, false);
  const fifth = h.running(5)();
  assert.match(h.calls[3].url, /running_skip=20/);
  assert.equal(h.view({ runningPage: 5 })[1].loading, true);
  h.resolvePair(2, "page-5");
  await fifth;
  const current = h.view({ runningPage: 5 })[1];
  assert.equal(current.loading, false);
  assert.equal(current.jobs[0].id, "page-5");
});

test("failed page changes never relabel old rows, and polling can recover", async () => {
  const h = harness();
  const first = h.running(1)();
  h.resolvePair(0, "page-1");
  await first;
  const fetch = h.running(5);
  const fifth = fetch();
  h.calls[2].resolve({ items: [], total: 0 });
  h.calls[3].reject(new Error("page load failed"));
  await fifth;
  const failed = h.view({ runningPage: 5 })[1];
  assert.equal(failed.loading, false);
  assert.deepEqual(failed.jobs, []);
  assert.equal(failed.emptyMessage, "cluster.loadFailed");
  const retry = fetch(true);
  h.resolvePair(4, "page-5");
  await retry;
  assert.equal(h.view({ runningPage: 5 })[1].jobs[0].id, "page-5");
});

test("same-page polling keeps visible rows without flashing a loader", async () => {
  const h = harness();
  const fetch = h.running(1);
  const first = fetch();
  h.resolvePair(0, "page-1");
  await first;
  const poll = fetch(true);
  for (const panel of h.view()) assert.equal(panel.loading, false);
  assert.equal(h.view()[1].jobs[0].id, "page-1");
  h.resolvePair(2, "page-1-refreshed");
  await poll;
});

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

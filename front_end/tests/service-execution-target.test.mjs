import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const text = readFileSync(new URL("../src/components/services/service-form.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("service-form.tsx", text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(tree) === "handleSave") callback = node.initializer.getText(tree);
  ts.forEachChild(node, visit);
}
visit(tree);
assert.ok(callback);

async function save({ targets = 2, capability = true, failure = false } = {}) {
  const calls = [];
  const errors = [];
  let saved = false;
  const bindings = {
    name: "Service", serviceId: "service", namespace: "test", repoName: "repo", hasScanned: true,
    selectedBranch: "main", selectedCommit: "commit", command: "run", target: { services_supported: true },
    EXECUTION_TARGETS: Array.from({ length: targets }, () => ({})), data: null, executionTarget: "second",
    description: "", requestTimeout: 60, idleTimeout: 30, maxConcurrency: 4, jobTaskName: "", jobDescription: "",
    gpuCount: 0, gpuType: "cpu", jobType: "A2", cpuCount: 1, memoryDemand: "1G", IS_PER_CPU_MEMORY: false,
    ephemeralStorage: "", runner: "", containerImage: "image", systemEntryCommand: "", isOriginalId: false,
    setErrorField() {}, setErrorMessage(error) { if (error) errors.push(error); }, scrollToError() {},
    t: key => key, document: { getElementById() { return null; } }, onSuccess() { saved = true; },
    async client(url, options) {
      calls.push({ url, options });
      if (url.endsWith("capabilities")) {
        if (failure) throw new Error("unavailable");
        return { execution_targets: capability };
      }
      return {};
    },
  };
  const compiled = ts.transpileModule(`return (${callback});`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  }).outputText;
  await new Function(...Object.keys(bindings), compiled)(...Object.values(bindings))();
  return { calls, errors, saved };
}

test("service target is never silently sent to an old backend", async () => {
  for (const failure of [false, true]) {
    const result = await save({ capability: false, failure });
    assert.equal(result.saved, false);
    assert.equal(result.calls.length, 1);
    assert.equal(result.errors.length, 1);
  }
});

test("capable backend receives the selected service target", async () => {
  const result = await save();
  assert.equal(result.saved, true);
  assert.equal(result.calls[1].options.json.execution_target, "second");
});

test("old single-target service submission does not require a new capability", async () => {
  const result = await save({ targets: 1 });
  assert.equal(result.saved, true);
  assert.equal(result.calls.length, 1);
  assert.equal(result.calls[0].options.json.execution_target, undefined);
});

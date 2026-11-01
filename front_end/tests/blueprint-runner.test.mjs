import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { webcrypto } from "node:crypto";
import ts from "typescript";

function callback(path, name, bindings) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const tree = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let code;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === name) code = node.initializer.getText(tree);
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(code, name);
  const compiled = ts.transpileModule(`return (${code});`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  }).outputText;
  return new Function(...Object.keys(bindings), compiled)(...Object.values(bindings));
}
const runner = "../src/components/blueprints/blueprint-runner.tsx";

function submission(overrides = {}) {
  const calls = [];
  const bindings = {
    blueprint: { id: "source" }, readyBlueprint: "source", submitting: { current: false },
    uploads: { current: new Set() }, paramsSchema: [], formValues: { input: 1 }, revisionRef: { current: "revision" },
    attemptRef: { current: null }, setErrorField() {}, setErrorMessage() {}, setIsRunning() {},
    validateFieldValue() { return null; }, scrollToError() {}, crypto: webcrypto,
    sessionStorage: { setItem() {} }, router: { refresh() {}, push() {} },
    async client(url, options) { calls.push({ url, options }); throw new Error("response lost"); },
    ...overrides,
  };
  return { bindings, calls, invoke: () => callback(runner, "handleRun", bindings)() };
}

test("run refuses an unloaded, failed, switched, or uploading form", async () => {
  for (const changes of [ { readyBlueprint: null }, { readyBlueprint: "previous" }, { uploads: { current: new Set(["upload"]) } } ]) {
    const state = submission(changes);
    await state.invoke();
    assert.equal(state.calls.length, 0);
  }
});

test("retry preserves key/body and changed input starts a separate acceptance", async () => {
  const state = submission();
  await state.invoke();
  await state.invoke();
  assert.deepEqual(state.calls[0].options, state.calls[1].options);
  assert.equal(state.calls[0].options.json.blueprint_revision, "revision");
  state.bindings.formValues = { input: 2 };
  await state.invoke();
  assert.notEqual(state.calls[1].options.headers["Idempotency-Key"], state.calls[2].options.headers["Idempotency-Key"]);
});

test("double activation cannot create simultaneous submissions", async () => {
  let release;
  let requests = 0;
  const state = submission({ client: () => { requests++; return new Promise(resolve => { release = resolve; }); } });
  const pending = state.invoke();
  await state.invoke();
  assert.equal(requests, 1);
  release({ job_id: "one" });
  await pending;
});

test("schema uses server canonical hash and revision before restoring preferences", async () => {
  let ready = null;
  let values = null;
  const revision = { current: null };
  const bindings = {
    blueprint: { id: "source" }, isMounted: true, revisionRef: revision, attemptRef: { current: null },
    submitting: { current: false }, setIsLoadingSchema() {}, setParamsSchema() {}, setErrorField() {}, setErrorMessage() {},
    setIsRunning() {}, setReadyBlueprint(value) { ready = value; }, setFormValues(value) { values = value; },
    t: key => key, computeStableHash() { throw new Error("must use canonical header"); },
    getFieldInitialValue: (field, cached) => cached ?? field.default,
    async client(url, options) {
      if (url.endsWith("schema")) {
        options.onResponse(new Response(null, { headers: { "X-Magnus-Blueprint-Signature": "canonical", "X-Magnus-Blueprint-Revision": "revision" } }));
        return [{ key: "dt", type: "float", default: 1e-7 }];
      }
      return { blueprint_hash: "canonical", cached_params: { dt: 2e-7 } };
    },
  };
  await callback(runner, "initialize", bindings)();
  assert.equal(ready, "source");
  assert.equal(revision.current, "revision");
  assert.equal(values.dt, 2e-7);
  bindings.client = async () => { throw new Error("unavailable"); };
  bindings.console = { error() {} };
  await callback(runner, "initialize", bindings)();
  assert.equal(ready, null);
});

test("replacement upload invalidates old receipt, blocks submit, ignores detached completion", async () => {
  let resolve;
  const updates = [];
  const busy = new Set();
  const bindings = {
    disabled: false, uploading: { current: false }, generation: { current: 0 }, uploadId: "input",
    reportActivity(id, active) { active ? busy.add(id) : busy.delete(id); },
    changeRef: { current(value) { updates.push(value); } }, setIsUploading() {}, setUploadMode() {}, setUploadError() {},
    fileInputRef: { current: null }, directoryInputRef: { current: null }, t: key => key,
    uploadFileToSecret: () => new Promise(done => { resolve = done; }),
  };
  const upload = callback("../src/components/ui/file-secret-input.tsx", "upload", bindings);
  const first = upload("file", [{}]);
  assert.deepEqual(updates, [""]);
  assert.equal(busy.size, 1);
  await upload("file", [{}]);
  resolve({ fileSecret: "new-receipt" });
  await first;
  assert.deepEqual(updates, ["", "new-receipt"]);
  assert.equal(busy.size, 0);
  const second = upload("file", [{}]);
  bindings.generation.current++;
  resolve({ fileSecret: "detached-receipt" });
  await second;
  assert.equal(updates.includes("detached-receipt"), false);
});

test("literal selections preserve numeric and string identities", () => {
  const source = readFileSync(new URL("../src/components/ui/dynamic-form/index.tsx", import.meta.url), "utf8");
  const tree = ts.createSourceFile("form.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let definition;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === "SingleFieldInput") definition = node.getText(tree);
    ts.forEachChild(node, visit);
  }
  visit(tree);
  const compiled = ts.transpileModule(`${definition}\nreturn SingleFieldInput;`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None, jsx: ts.JsxEmit.React },
  }).outputText;
  const React = { createElement(type, props, ...children) { return { type, props, children }; } };
  const render = new Function("React", "cn", "SearchableSelect", compiled)(React, () => "", "select");
  const field = { type: "select", options: [{ value: 1, label: "number" }, { value: "1", label: "text" }] };
  let chosen;
  const treeResult = render({ field, value: 1, onChange: value => { chosen = value; } });
  const select = treeResult.children[0].props;
  assert.equal(select.value, "0");
  select.onChange("1");
  assert.equal(chosen, "1");
  select.onChange("0");
  assert.equal(chosen, 1);
  select.onChange("");
  assert.equal(chosen, undefined);
  assert.equal(render({ field, value: undefined }).children[0].props.value, "");
  const boolean = render({ field: { type: "boolean" }, value: true, onChange: value => { chosen = value; } }).children[0].props;
  boolean.onChange("");
  assert.equal(chosen, undefined);
});

test("cleared choices, fractional integers and nonfinite floats cannot submit", () => {
  const source = readFileSync(new URL("../src/components/ui/dynamic-form/types.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const exports = {};
  new Function("exports", compiled)(exports);
  const validate = exports.validateFieldValue;
  assert.ok(validate({ key: "choice", type: "select", options: [{ value: 1 }] }, undefined));
  assert.ok(validate({ key: "choice", type: "boolean" }, undefined));
  assert.ok(validate({ key: "n", type: "number" }, 1.5));
  assert.ok(validate({ key: "x", type: "float" }, "Infinity"));
  assert.equal(validate({ key: "choice", type: "select", options: [{ value: 1 }] }, 1), null);
});

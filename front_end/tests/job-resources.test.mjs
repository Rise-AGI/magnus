import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function loadSource(path, overrides = {}, suffix = "") {
  const source = readFileSync(new URL(path, import.meta.url), "utf8") + suffix;
  const code = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", code)(
    id => overrides[id] ?? require(id), module, module.exports,
  );
  return module.exports;
}

// Exercise production formatting, component markup, and both translation sets,
// without starting Next, opening a browser, or connecting to a Magnus database.
const resources = loadSource("../src/lib/job-resources.ts");
const { translations } = loadSource("../src/context/language-context.tsx", {}, "\nexport { translations };\n");
const translate = language => (key, params = {}) => {
  assert.ok(translations[key]?.[language], `Missing ${language} translation: ${key}`);
  return Object.entries(params).reduce(
    (text, [name, value]) => text.split(`{${name}}`).join(value), translations[key][language],
  );
};
const GiB = 1024 ** 3;
const base = {
  cpu_count: 1, memory_demand: "4000M", gpu_count: 0, gpu_type: "cpu",
  node_count: 2, tasks_per_node: 64,
  resource_summary: {
    basis: "request", mode: "slurm_per_cpu", mode_inferred: false,
    cpu_count: 128, memory_bytes: 500 * GiB, gpu_count: 0,
    node_count: 2, tasks_per_node: 64,
  },
};
const withSummary = summary => ({ ...base, resource_summary: { ...base.resource_summary, ...summary } });
const present = (job, language = "en") => resources.presentJobResources(job, translate(language));

test("multi-task request shows whole-job CPU and binary RAM", () => {
  const view = present(base);
  assert.deepEqual(view.values, ["CPU × 128", "RAM 500 GiB"]);
});

test("per-node memory and GPU totals are not multiplied by task count", () => {
  const view = present({
    ...withSummary({ mode: "slurm_per_node", memory_bytes: 100 * GiB, gpu_count: 4 }),
    gpu_type: "a100_80g", gpu_count: 2, memory_demand: "50G",
  });
  assert.deepEqual(view.values, ["CPU × 128", "RAM 100 GiB", "a100 80g × 4"]);
});

test("SLURM allocation totals are never multiplied by raw request fields", () => {
  const view = present(withSummary({ basis: "allocation", cpu_count: 256, memory_bytes: 1024 * GiB, tasks_per_node: null }));
  assert.deepEqual(view.values, ["CPU × 256", "RAM 1 TiB"]);
});

test("unknown allocation quantities stay unknown", () => {
  const view = present(withSummary({ basis: "allocation", cpu_count: null, memory_bytes: null, node_count: null, tasks_per_node: null }));
  assert.deepEqual(view.values, ["CPU × Unknown", "RAM Unknown"]);
});

test("historical summaries retain their resource totals", () => {
  for (const summary of [
    { mode_inferred: true },
    { basis: "allocation", allocation_inferred: true, memory_source: "per_node_fallback", tasks_per_node: null },
  ]) {
    assert.deepEqual(present(withSummary(summary)).values, ["CPU × 128", "RAM 500 GiB"]);
  }
});

test("older APIs preserve raw values without guessing a memory mode", () => {
  const view = present({ ...base, resource_summary: undefined });
  assert.deepEqual(view.values, ["CPU × 1", "RAM 4000M"]);
});

test("local requests do not manufacture multi-node allocation", () => {
  const view = present(withSummary({ mode: "local", cpu_count: 1, memory_bytes: 4000 * 1024 ** 2, node_count: 1, tasks_per_node: null }));
  assert.deepEqual(view.values, ["CPU × 1", "RAM 4000 MiB"]);
});

test("unspecified resources and all-node-memory requests do not become zero-byte allocations", () => {
  const job = { ...withSummary({ mode: "slurm_per_node", cpu_count: null, memory_bytes: null }), cpu_count: null, memory_demand: "0M" };
  assert.deepEqual(present(job).values, ["CPU × Default", "RAM All node memory"]);
  assert.deepEqual(present({ ...job, memory_demand: null }).values, ["CPU × Default", "RAM Default"]);
  assert.equal(resources.formatResourceMemory(0), "0 B");
});

test("summary-derived totals take precedence when raw request differs", () => {
  const view = present(withSummary({ cpu_count: 256, memory_bytes: 1000 * GiB }));
  assert.deepEqual(view.values, ["CPU × 256", "RAM 1000 GiB"]);
});

test("table and detail show resource values without annotations or hidden tooltips", () => {
  const { JobResources } = loadSource("../src/components/jobs/job-resources.tsx", {
    "@/lib/job-resources": resources,
    "@/context/language-context": { useLanguage: () => ({ t: translate("zh") }) },
  });
  const table = renderToStaticMarkup(React.createElement(JobResources, { job: base }));
  const detail = renderToStaticMarkup(React.createElement(JobResources, { job: base, expanded: true }));
  for (const html of [table, detail]) {
    assert.match(html, /CPU × 128/);
    assert.match(html, /RAM 500 GiB/);
    assert.equal(html.replace(/<[^>]+>/g, ""), "CPU × 128RAM 500 GiB");
    assert.doesNotMatch(html, /title=|aria-label=/);
  }
});

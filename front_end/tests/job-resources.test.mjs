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

test("multi-task request shows whole-job CPU and binary RAM with matching breakdown", () => {
  const view = present(base);
  assert.deepEqual(view.values, ["CPU × 128", "RAM 500 GiB"]);
  assert.equal(view.scope, "Requested total");
  assert.ok(view.details.includes("Requested topology: 2 nodes × 64 tasks/node"));
  assert.ok(view.details.includes("CPU/task: 1"));
  assert.ok(view.details.includes("RAM/task: 4000 MiB (per-CPU allocation)"));
});

test("per-node memory and GPU breakdown use nodes, not task count", () => {
  const view = present({
    ...withSummary({ mode: "slurm_per_node", memory_bytes: 100 * GiB, gpu_count: 4 }),
    gpu_type: "a100_80g", gpu_count: 2, memory_demand: "50G",
  });
  assert.deepEqual(view.values, ["CPU × 128", "RAM 100 GiB", "a100 80g × 4"]);
  assert.ok(view.details.includes("RAM/node: 50 GiB"));
  assert.ok(view.details.includes("GPU/node: 2"));
});

test("SLURM allocation totals are never multiplied or explained with raw request fields", () => {
  const view = present(withSummary({ basis: "allocation", cpu_count: 256, memory_bytes: 1024 * GiB, tasks_per_node: null }));
  assert.deepEqual(view.values, ["CPU × 256", "RAM 1 TiB"]);
  assert.equal(view.scope, "Allocated total");
  assert.ok(view.details.includes("Allocated nodes: 2"));
  assert.ok(!view.details.join(" ").includes("CPU/task"));
  assert.ok(!view.details.join(" ").includes("4000"));
  assert.ok(!view.details.join(" ").includes("Requested topology"));
});

test("unknown allocation quantities stay unknown, including node count", () => {
  const view = present(withSummary({ basis: "allocation", cpu_count: null, memory_bytes: null, node_count: null, tasks_per_node: null }));
  assert.deepEqual(view.values, ["CPU × Unknown", "RAM Unknown"]);
  assert.ok(view.details.includes("Allocated nodes: Unknown"));
  assert.ok(!view.details.join(" ").includes("null"));
});

test("historical inferred mode is visibly distinguished and explained in both languages", () => {
  const job = withSummary({ mode_inferred: true });
  assert.match(present(job).scope, /inferred/);
  assert.match(present(job).details.join(" "), /inferred from current station settings/);
  assert.match(present(job, "zh").scope, /估算/);
  assert.match(present(job, "zh").details.join(" "), /按当前站点配置推算/);
});

test("older APIs preserve raw values and explicitly withhold whole-job scope", () => {
  const view = present({ ...base, resource_summary: undefined });
  assert.deepEqual(view.values, ["CPU × 1", "RAM 4000M"]);
  assert.equal(view.scope, "Raw values (scope unavailable)");
  assert.match(view.details.join(" "), /do not interpret them as whole-job totals/);
});

test("local requests do not manufacture multi-node allocation", () => {
  const view = present(withSummary({ mode: "local", cpu_count: 1, memory_bytes: 4000 * 1024 ** 2, node_count: 1, tasks_per_node: null }));
  assert.deepEqual(view.values, ["CPU × 1", "RAM 4000 MiB"]);
  assert.equal(view.scope, "Requested values");
  assert.match(view.details.join(" "), /not enforced allocation limits/);
  assert.ok(!view.details.join(" ").includes("64"));
  assert.match(present(withSummary({ mode: "local", mode_inferred: true })).details.join(" "), /inferred from current station settings/);
});

test("unspecified resources and all-node-memory requests do not become zero-byte allocations", () => {
  const job = { ...withSummary({ mode: "slurm_per_node", cpu_count: null, memory_bytes: null }), cpu_count: null, memory_demand: "0M" };
  assert.deepEqual(present(job).values, ["CPU × Unspecified / default unknown", "RAM All node memory (total unknown)"]);
  assert.deepEqual(present({ ...job, memory_demand: null }).values, ["CPU × Unspecified / default unknown", "RAM Unspecified / default unknown"]);
  assert.equal(resources.formatResourceMemory(0), "0 B");
});

test("summary-derived breakdown remains consistent when raw request differs", () => {
  const view = present(withSummary({ cpu_count: 256, memory_bytes: 1000 * GiB }));
  assert.ok(view.details.includes("CPU/task: 2"));
  assert.ok(view.details.includes("RAM/task: 8000 MiB (per-CPU allocation)"));
});

test("shared table and detail component render totals and expose their explanations", () => {
  const { JobResources } = loadSource("../src/components/jobs/job-resources.tsx", {
    "@/lib/job-resources": resources,
    "@/context/language-context": { useLanguage: () => ({ t: translate("zh") }) },
  });
  const table = renderToStaticMarkup(React.createElement(JobResources, { job: base }));
  const detail = renderToStaticMarkup(React.createElement(JobResources, { job: base, expanded: true }));
  for (const html of [table, detail]) {
    assert.match(html, /CPU × 128/);
    assert.match(html, /RAM 500 GiB/);
    assert.match(html, /请求总量/);
    assert.match(html, /2 节点 × 64 tasks \/ 节点/);
  }
  assert.match(table, /title=/);
  assert.match(table, /aria-label=/);
  assert.match(detail, /<p>CPU \/ task：1<\/p>/);
});

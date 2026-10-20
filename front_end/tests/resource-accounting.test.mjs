import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
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

const jobResources = loadSource("../src/lib/job-resources.ts");
const { presentClusterResources } = loadSource("../src/lib/cluster-resources.ts", { "@/lib/job-resources": jobResources });
const { metricLabelsKey, metricSeriesName } = loadSource("../src/lib/metric-series.ts");
const { translations } = loadSource("../src/context/language-context.tsx", {}, "\nexport { translations };\n");
const translate = language => (key, params = {}) => {
  assert.ok(translations[key]?.[language], `Missing ${language} translation: ${key}`);
  return Object.entries(params).reduce(
    (text, [name, value]) => text.split(`{${name}}`).join(value), translations[key][language],
  );
};
const t = translate("en");
const snapshot = {
  total: 8, used: 2, free: 4, cpu_total: 128, cpu_free: 64,
  mem_total_mb: 512000, mem_free_mb: 256000, gpu_model: "A100", node: "partition-name",
  accounting_scope: "partition", observed_at: "2026-10-19T12:00:00+00:00",
  is_stale: false, allocation_tracked: true, free_is_lower_bound: false,
  unavailable_gpus: 0, unavailable_cpus: 0, unavailable_mem_mb: 0,
};

test("cluster resource numbers keep binary units and allocation semantics", () => {
  const view = presentClusterResources(snapshot, t);
  assert.equal(view.cpuFree, "64");
  assert.equal(view.gpuFree, "4");
  assert.equal(view.gpuAllocated, "2");
  assert.equal(view.memoryFree, "250 GiB");
  assert.equal(view.memoryTotal, "500 GiB");
});

test("local allocation-untracked capacity never appears fully free", () => {
  const view = presentClusterResources({ ...snapshot, accounting_scope: "local", allocation_tracked: false, cpu_free: 128, free: 8, mem_free_mb: 512000 }, t);
  assert.equal(view.cpuFree, "Unknown");
  assert.equal(view.gpuFree, "Unknown");
  assert.equal(view.gpuAllocated, "Unknown");
  assert.equal(view.memoryFree, "Unknown");
  assert.equal(view.memoryTotal, "500 GiB");
});

test("unavailable capacity preserves lower-bound free counts without changing totals", () => {
  const view = presentClusterResources({ ...snapshot, free_is_lower_bound: true, unavailable_cpus: 32, unavailable_gpus: 2, unavailable_mem_mb: 128000 }, t);
  assert.equal(view.cpuFree, "≥ 64");
  assert.equal(view.gpuFree, "≥ 4");
  assert.equal(view.memoryFree, "≥ 250 GiB");
  assert.equal(view.memoryTotal, "500 GiB");
});

test("backend stale data and a failed frontend refresh both mark retained values stale", () => {
  for (const view of [presentClusterResources({ ...snapshot, is_stale: true }, t), presentClusterResources(snapshot, t, true)]) {
    assert.equal(view.stale, true);
    assert.equal(view.cpuFree, "64");
    assert.equal(view.memoryFree, "250 GiB");
  }
});

test("cross-boundary whole-job allocations are shown as upper bounds", () => {
  const view = presentClusterResources({ ...snapshot, usage_is_upper_bound: true, free_is_lower_bound: true }, t);
  assert.equal(view.gpuAllocated, "≤ 2");
  assert.equal(view.gpuFree, "≥ 4");
});

test("inferred allocation uses approximate values instead of strict cross-pool bounds", () => {
  const view = presentClusterResources({ ...snapshot, allocation_inferred: true, usage_is_upper_bound: true, free_is_lower_bound: true }, t);
  assert.equal(view.gpuAllocated, "≈ 2");
  assert.equal(view.gpuFree, "≈ 4");
  assert.equal(view.cpuFree, "≈ 64");
  assert.equal(view.memoryFree, "≈ 250 GiB");
  assert.equal(view.memoryTotal, "500 GiB");
});

test("old API values remain readable without freshness and accounting metadata", () => {
  const { observed_at, is_stale, allocation_tracked, accounting_scope, ...old } = snapshot;
  const view = presentClusterResources(old, t);
  assert.equal(view.cpuFree, "64");
  assert.equal(view.gpuFree, "4");
  assert.equal(view.memoryTotal, "500 GiB");
});

test("unknown resource values have Chinese translations", () => {
  const view = presentClusterResources({ ...snapshot, allocation_tracked: false }, translate("zh"));
  assert.equal(view.cpuFree, "未知");
  assert.equal(view.gpuFree, "未知");
  assert.equal(view.memoryFree, "未知");
});

test("system series names identify devices and ranks without exposing telemetry metadata", () => {
  const labels = { node: "compute01", device: "cuda:0", rank: "3", scope: "device", coverage: "partial", normalized_by: "allocated_cpus", normalization_cpus: "128", source: "jobacct" };
  assert.equal(metricSeriesName("system.gpu.utilization", labels), "compute01 · cuda:0 · rank 3");
  assert.equal(metricSeriesName("system.cpu.utilization", { scope: "job", source: "jobacct", normalization_cpus: "128" }), "");
  assert.equal(metricSeriesName("train.loss", { source: "validation", rank: "0" }), "rank=0, source=validation");
});

test("series storage keeps full labels and cannot collide on label punctuation", () => {
  assert.equal(metricLabelsKey({ node: "compute01", device: "cuda:0" }), metricLabelsKey({ device: "cuda:0", node: "compute01" }));
  assert.notEqual(metricLabelsKey({ a: "1,b=2" }), metricLabelsKey({ a: "1", b: "2" }));
  assert.notEqual(metricLabelsKey({ scope: "job" }), metricLabelsKey({ scope: "batch_step" }));
});

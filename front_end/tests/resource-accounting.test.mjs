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
const { metricScopeNotes } = loadSource("../src/lib/metric-scope.ts");
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
  assert.equal(view.scope, "Current partition node pool");
  assert.equal(view.observedAt, snapshot.observed_at);
  assert.match(view.notes.join(" "), /not guaranteed immediate availability or live utilization/);
});

test("local allocation-untracked capacity never appears fully free", () => {
  const view = presentClusterResources({ ...snapshot, accounting_scope: "local", allocation_tracked: false, cpu_free: 128, free: 8, mem_free_mb: 512000 }, t);
  assert.equal(view.cpuFree, "Unknown");
  assert.equal(view.gpuFree, "Unknown");
  assert.equal(view.gpuAllocated, "Unknown");
  assert.equal(view.memoryFree, "Unknown");
  assert.equal(view.memoryTotal, "500 GiB");
  assert.match(view.notes.join(" "), /does not track resource allocation/);
});

test("unavailable capacity explains lower-bound free counts without changing totals", () => {
  const view = presentClusterResources({ ...snapshot, free_is_lower_bound: true, unavailable_cpus: 32, unavailable_gpus: 2, unavailable_mem_mb: 128000 }, t);
  assert.equal(view.cpuFree, "≥ 64");
  assert.equal(view.gpuFree, "≥ 4");
  assert.equal(view.memoryFree, "≥ 250 GiB");
  assert.equal(view.memoryTotal, "500 GiB");
  assert.match(view.notes.join(" "), /Unschedulable capacity: CPU × 32, RAM 125 GiB, GPU × 2/);
  assert.match(view.notes.join(" "), /included in configured totals/);
});

test("backend stale data and a failed frontend refresh both disclose old observations", () => {
  for (const view of [presentClusterResources({ ...snapshot, is_stale: true }, t), presentClusterResources(snapshot, t, true)]) {
    assert.equal(view.stale, true);
    assert.equal(view.observedAt, snapshot.observed_at);
    assert.match(view.notes.join(" "), /not current availability/);
  }
});

test("cross-boundary whole-job allocations are shown as upper bounds", () => {
  const view = presentClusterResources({ ...snapshot, usage_is_upper_bound: true, free_is_lower_bound: true }, t);
  assert.equal(view.gpuAllocated, "≤ 2");
  assert.equal(view.gpuFree, "≥ 4");
  assert.match(view.notes.join(" "), /competing jobs are counted in full/);
  assert.match(view.notes.join(" "), /allocations outside this node pool/);
  assert.match(presentClusterResources({ ...snapshot, usage_is_upper_bound: true }, translate("zh")).notes.join(" "), /节点池以外的分配或挂起前的 CPU 分配/);
});

test("inferred allocation uses approximate values instead of strict cross-pool bounds", () => {
  const view = presentClusterResources({ ...snapshot, allocation_inferred: true, usage_is_upper_bound: true, free_is_lower_bound: true }, t);
  assert.equal(view.gpuAllocated, "≈ 2");
  assert.equal(view.gpuFree, "≈ 4");
  assert.equal(view.cpuFree, "≈ 64");
  assert.equal(view.memoryFree, "≈ 250 GiB");
  assert.equal(view.memoryTotal, "500 GiB");
  assert.match(view.notes.join(" "), /inferred from scheduler request fields/);
  assert.match(view.notes.join(" "), /allocations outside this node pool/);
  assert.ok(!view.notes.join(" ").includes("≥"));
  assert.ok(!view.notes.join(" ").includes("≤"));
});

test("old API values remain readable with unknown freshness and accounting scope", () => {
  const { observed_at, is_stale, allocation_tracked, accounting_scope, ...old } = snapshot;
  const view = presentClusterResources(old, t);
  assert.equal(view.cpuFree, "64");
  assert.equal(view.observedAt, null);
  assert.equal(view.scope, "Accounting scope unavailable");
  assert.match(view.notes.join(" "), /does not provide complete observation/);
});

test("batch-step CPU telemetry identifies omitted MPI ranks and per-task normalization", () => {
  const notes = metricScopeNotes("system.cpu.utilization", [{ labels: { node: "compute01", scope: "batch_step", coverage: "partial", normalized_by: "cpus_per_task", normalization_cpus: "1.0" } }], { node_count: 2, tasks_per_node: 64 }, t);
  assert.match(notes.join(" "), /batch-step cgroup on the first node/);
  assert.match(notes.join(" "), /not whole-job resource usage/);
  assert.match(notes.join(" "), /denominator: 1 CPUs \(per-task allocation\)/);
  assert.match(notes.join(" "), /Sampled nodes: compute01/);
});

test("CPU denominator follows sampled node, host fallback, or container quota labels", () => {
  for (const [basis, count, expected] of [
    ["cpus_on_node", "64.0", /64 CPUs \(allocation on the sampled node\)/],
    ["host_cpu_count", "128.0", /128 CPUs \(fallback to all CPUs on the sampled host\)/],
    ["cgroup_quota", "0.5", /0.5 CPUs \(container cgroup quota\)/],
  ]) {
    const notes = metricScopeNotes("system.cpu.utilization", [{ labels: { scope: "container", normalized_by: basis, normalization_cpus: count } }], undefined, t);
    assert.match(notes.join(" "), expected);
  }
  const mixed = metricScopeNotes("system.cpu.utilization", [
    { labels: { scope: "batch_step", normalized_by: "cpus_per_task", normalization_cpus: "1.0" } },
    { labels: { scope: "batch_step", normalized_by: "cpus_per_task", normalization_cpus: "4.0" } },
  ], undefined, t);
  assert.match(mixed.join(" "), /denominator: 1 CPUs/);
  assert.match(mixed.join(" "), /denominator: 4 CPUs/);
  const legacy = metricScopeNotes("system.cpu.utilization", [{ labels: { normalized_by: "cpus_per_task" } }], undefined, t);
  assert.match(legacy.join(" "), /CPUs per task, not the whole-job CPU allocation/);
  assert.ok(!legacy.join(" ").includes("NaN"));
});

test("GPU telemetry remains device-level even on a single node and single stream", () => {
  const notes = metricScopeNotes("system.gpu.memory.used_bytes", [{ labels: { node: "compute01", scope: "device", coverage: "single_task" } }], { node_count: 1, tasks_per_node: 1 }, t);
  assert.match(notes.join(" "), /may include other processes/);
  assert.match(notes.join(" "), /Sampled nodes: compute01/);
  assert.ok(!notes.join(" ").includes("multi-node"));
});

test("historical unlabelled multi-node telemetry carries partial and unknown-scope warnings", () => {
  const job = { node_count: 2, tasks_per_node: 64 };
  const cpu = metricScopeNotes("system.memory.used_bytes", [{ labels: { node: "compute01" } }], job, t);
  assert.match(cpu.join(" "), /not whole-job resource usage/);
  assert.match(cpu.join(" "), /whole-job coverage cannot be confirmed/);
  const gpu = metricScopeNotes("system.gpu.utilization", [{ labels: { node: "compute01" } }], job, t);
  assert.match(gpu.join(" "), /only the first node/);
});

test("single-container telemetry is identified without an MPI warning", () => {
  const notes = metricScopeNotes("system.cpu.utilization", [{ labels: { scope: "container", coverage: "single_container" } }], undefined, t);
  assert.match(notes.join(" "), /individual runner container/);
  assert.ok(!notes.join(" ").includes("MPI"));
});

test("unverified cgroup telemetry is not presented as batch-step or container usage", () => {
  const notes = metricScopeNotes("system.cpu.utilization", [{ labels: { scope: "unverified_cgroup", coverage: "unverified" } }], { node_count: 1, tasks_per_node: 1 }, t);
  assert.match(notes.join(" "), /may cover the host or a service group/);
  assert.match(notes.join(" "), /cannot be attributed to this job/);
  assert.ok(!notes.join(" ").includes("batch-step cgroup"));
  assert.ok(!notes.join(" ").includes("runner container"));
  assert.match(metricScopeNotes("system.memory.used_bytes", [{ labels: { scope: "job_node" } }], undefined, t).join(" "), /excluding other nodes/);
  assert.match(metricScopeNotes("system.memory.used_bytes", [{ labels: { scope: "slurm_step" } }], undefined, t).join(" "), /one SLURM execution step/);
});

test("user metrics are not assigned a fabricated system scope", () => {
  assert.deepEqual(metricScopeNotes("train.loss", [{ labels: { node: "compute01" } }], { node_count: 2 }, t), []);
});

test("resource and telemetry explanations have Chinese translations", () => {
  const zh = translate("zh");
  assert.match(presentClusterResources({ ...snapshot, is_stale: true, free_is_lower_bound: true }, zh).notes.join(" "), /保守余量下界/);
  assert.match(metricScopeNotes("system.cpu.utilization", [{ labels: { scope: "batch_step", coverage: "partial" } }], { node_count: 2 }, zh).join(" "), /不是整个 Job/);
});

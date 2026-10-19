import type { useLanguage } from "@/context/language-context";
import type { Job } from "@/types/job";

type Translate = ReturnType<typeof useLanguage>["t"];
type ResourceJob = Pick<Job, "cpu_count" | "memory_demand" | "gpu_count" | "gpu_type" | "node_count" | "tasks_per_node" | "resource_summary">;

/** SLURM memory uses binary units; keep MiB/GiB explicit in the display. */
export function formatResourceMemory(bytes: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let exponent = bytes > 0 ? Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1) : 0;
  // Keep request arithmetic exact: 4000 MiB × 128 is visibly 500 GiB,
  // while a rounded 3.91 GiB per task would imply a different total.
  while (exponent > 0 && !Number.isInteger(bytes / 1024 ** exponent)) exponent--;
  return `${bytes / 1024 ** exponent} ${units[exponent]}`;
}

export function presentJobResources(job: ResourceJob, t: Translate) {
  const summary = job.resource_summary;
  const isAllocation = summary?.basis === "allocation";
  const isLocal = summary?.mode === "local";
  const cpu = summary ? summary.cpu_count : job.cpu_count;
  const gpu = summary ? summary.gpu_count : job.gpu_count;
  const unknown = t(isAllocation ? "jobResources.unknown" : "jobResources.defaultUnknown");
  const allNodeMemory = !isAllocation && !isLocal && summary?.mode === "slurm_per_node"
    && /^0+(?:\.0+)?\s*(?:[KMGTPE]i?B?|B)?$/i.test(job.memory_demand?.trim() ?? "");
  const memory = summary
    ? (summary.memory_bytes == null ? (allNodeMemory ? t("jobResources.allNodeMemory") : unknown) : formatResourceMemory(summary.memory_bytes))
    : (job.memory_demand ?? unknown);
  const scope = !summary
    ? t("jobResources.legacyValues")
    : isAllocation
      ? t(summary.allocation_inferred ? "jobResources.estimatedAllocation" : "jobResources.allocatedTotal")
      : isLocal
        ? t("jobResources.requestValues")
        : t(summary.mode_inferred ? "jobResources.inferredTotal" : "jobResources.requestedTotal");
  const details: string[] = [];
  if (!summary) {
    // An older API does not identify whether RAM is per node or per task.
    // In particular, never multiply external rows that already contain totals.
    details.push(t("jobResources.legacyExplanation"));
    if ((job.node_count ?? 1) > 1 || (job.tasks_per_node ?? 1) > 1) {
      details.push(t("jobResources.requestTopology", {
        nodes: String(job.node_count ?? 1), tasks: String(job.tasks_per_node ?? 1),
      }));
    }
  } else if (isAllocation) {
    details.push(t(summary.allocation_inferred ? "jobResources.estimatedAllocationExplanation" : "jobResources.allocationExplanation"));
    details.push(t("jobResources.allocatedNodes", { nodes: summary.node_count == null ? unknown : String(summary.node_count) }));
    const state = summary.slurm_state?.toUpperCase();
    if (state && state !== "RUNNING") {
      const stateDescriptions = {
        SUSPENDED: "jobResources.slurmSuspended",
        STOPPED: "jobResources.slurmStopped",
        CONFIGURING: "jobResources.slurmConfiguring",
        COMPLETING: "jobResources.slurmCompleting",
      } as const;
      details.push(t("jobResources.slurmState", { state }));
      if (state in stateDescriptions) details.push(t(stateDescriptions[state as keyof typeof stateDescriptions]));
    }
    // Request fields can differ from scheduler allocation. Do not present them
    // as an observed CPU/task or memory/node distribution.
  } else if (isLocal) {
    details.push(t("jobResources.localExplanation"));
  } else {
    details.push(t("jobResources.requestExplanation"));
    details.push(t("jobResources.requestTopology", {
      nodes: String(summary.node_count), tasks: String(summary.tasks_per_node ?? 1),
    }));
    const nodes = summary.node_count ?? 1;
    const tasks = nodes * (summary.tasks_per_node ?? 1);
    details.push(t("jobResources.cpuPerTask", { cpu: cpu == null ? unknown : String(cpu / tasks) }));
    details.push(t(summary.mode === "slurm_per_cpu" ? "jobResources.memoryPerTask" : "jobResources.memoryPerNode", {
      memory: summary.memory_bytes == null ? memory : formatResourceMemory(summary.memory_bytes / (summary.mode === "slurm_per_cpu" ? tasks : nodes)),
    }));
    if (gpu > 0) details.push(t("jobResources.gpuPerNode", { gpu: String(gpu / nodes) }));
  }
  if (summary?.mode_inferred && !isAllocation) details.push(t("jobResources.modeInferred"));
  const gpuType = job.gpu_type.toLowerCase() === "cpu" ? "GPU" : job.gpu_type.replace(/_/g, " ");
  const values = [`CPU × ${cpu ?? unknown}`, `RAM ${memory}`];
  if (gpu > 0) values.push(`${gpuType} × ${gpu}`);
  return { scope, values, details };
}

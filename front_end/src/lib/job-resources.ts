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
  const gpuType = job.gpu_type.toLowerCase() === "cpu" ? "GPU" : job.gpu_type.replace(/_/g, " ");
  const values = [`CPU × ${cpu ?? unknown}`, `RAM ${memory}`];
  if (gpu > 0) values.push(`${gpuType} × ${gpu}`);
  return { values };
}

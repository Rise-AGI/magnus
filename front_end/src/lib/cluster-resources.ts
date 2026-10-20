import type { useLanguage } from "@/context/language-context";
import { formatResourceMemory } from "@/lib/job-resources";

export interface ClusterResources {
  node: string;
  gpu_model: string;
  total: number;
  free: number;
  used: number;
  cpu_total: number;
  cpu_free: number;
  mem_total_mb: number;
  mem_free_mb: number;
  observed_at?: string | null;
  is_stale?: boolean;
  free_is_lower_bound?: boolean;
  usage_is_upper_bound?: boolean;
  allocation_tracked?: boolean;
  allocation_inferred?: boolean;
  accounting_scope?: "partition" | "cluster" | "local";
  unavailable_gpus?: number;
  unavailable_cpus?: number;
  unavailable_mem_mb?: number;
  node_count?: number | null;
}

export function presentClusterResources(
  resources: ClusterResources,
  t: ReturnType<typeof useLanguage>["t"],
  refreshFailed = false,
) {
  const stale = refreshFailed || resources.is_stale === true;
  const tracked = resources.allocation_tracked !== false;
  const lowerBound = tracked && resources.free_is_lower_bound === true;
  const inferred = tracked && resources.allocation_inferred === true;
  const unknown = t("jobResources.unknown");
  const prefix = inferred ? "≈ " : lowerBound ? "≥ " : "";
  return {
    stale,
    cpuFree: tracked ? `${prefix}${resources.cpu_free}` : unknown,
    gpuFree: tracked ? `${prefix}${resources.free}` : unknown,
    gpuAllocated: tracked ? `${inferred ? "≈ " : resources.usage_is_upper_bound ? "≤ " : ""}${resources.used}` : unknown,
    memoryFree: tracked ? `${prefix}${formatResourceMemory(resources.mem_free_mb * 1024 ** 2)}` : unknown,
    memoryTotal: formatResourceMemory(resources.mem_total_mb * 1024 ** 2),
  };
}

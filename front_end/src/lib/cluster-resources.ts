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
  gpu_used?: number | null;
  cpu_used?: number | null;
  mem_used_mb?: number | null;
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
  const oldFreeIsExact = tracked && !resources.free_is_lower_bound && !resources.allocation_inferred;
  const oldUsageIsExact = tracked && !resources.usage_is_upper_bound && !resources.allocation_inferred;
  const unknown = t("jobResources.unknown");
  const cpu = resources.cpu_used !== undefined ? resources.cpu_used
    : oldFreeIsExact ? Math.max(0, resources.cpu_total - resources.cpu_free) : null;
  const gpu = resources.gpu_used !== undefined ? resources.gpu_used
    : oldUsageIsExact ? resources.used : null;
  const memory = resources.mem_used_mb !== undefined ? resources.mem_used_mb
    : oldFreeIsExact ? Math.max(0, resources.mem_total_mb - resources.mem_free_mb) : null;
  return {
    stale,
    cpuFree: cpu == null ? unknown : `${Math.max(0, resources.cpu_total - cpu)}`,
    gpuFree: gpu == null ? unknown : `${Math.max(0, resources.total - gpu)}`,
    memoryFree: memory == null ? unknown : formatResourceMemory(Math.max(0, resources.mem_total_mb - memory) * 1024 ** 2),
    memoryTotal: formatResourceMemory(resources.mem_total_mb * 1024 ** 2),
  };
}

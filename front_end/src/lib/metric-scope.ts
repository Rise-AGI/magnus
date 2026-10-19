import type { useLanguage } from "@/context/language-context";
import type { Job } from "@/types/job";

export function metricScopeNotes(
  name: string | null,
  streams: { labels: Record<string, string> }[],
  job: Pick<Job, "node_count" | "tasks_per_node"> | undefined,
  t: ReturnType<typeof useLanguage>["t"],
): string[] {
  if (!name?.startsWith("system.")) return [];
  const notes: string[] = [];
  const scopes = new Set(streams.map(stream => stream.labels.scope).filter(Boolean));
  const missingScope = streams.some(stream => !stream.labels.scope);
  const partial = streams.some(stream => stream.labels.coverage === "partial");
  const multiTask = (job?.node_count ?? 1) * (job?.tasks_per_node ?? 1) > 1;
  if (name.startsWith("system.gpu.")) {
    notes.push(t("jobDetail.metricsGpuScope"));
    if ((job?.node_count ?? 1) > 1 || (partial && job?.node_count == null)) notes.push(t("jobDetail.metricsFirstNode"));
  } else if (name.startsWith("system.cpu.") || name.startsWith("system.memory.")) {
    if (scopes.has("batch_step")) notes.push(t("jobDetail.metricsBatchScope"));
    if (scopes.has("container")) notes.push(t("jobDetail.metricsContainerScope"));
    if (scopes.has("job_node")) notes.push(t("jobDetail.metricsJobNodeScope"));
    if (scopes.has("slurm_step")) notes.push(t("jobDetail.metricsSlurmStepScope"));
    if (scopes.has("unverified_cgroup") || streams.some(stream => stream.labels.coverage === "unverified")) {
      notes.push(t("jobDetail.metricsUnverifiedScope"));
    }
    if (partial || (multiTask && missingScope)) notes.push(t("jobDetail.metricsPartialRanks"));
    if (name.startsWith("system.cpu.")) {
      const bases = {
        cpus_per_task: "jobDetail.metricsCpuBasisTask",
        cpus_on_node: "jobDetail.metricsCpuBasisNode",
        host_cpu_count: "jobDetail.metricsCpuBasisHost",
        cgroup_quota: "jobDetail.metricsCpuBasisContainer",
      } as const;
      const normalizationNotes = streams.map(stream => {
        const count = Number(stream.labels.normalization_cpus);
        const basis = stream.labels.normalized_by;
        if (Number.isFinite(count) && count > 0) {
          return t("jobDetail.metricsCpuDenominator", {
            count: String(count),
            basis: t(Object.keys(bases).includes(basis) ? bases[basis as keyof typeof bases] : "jobDetail.metricsCpuBasisUnknown"),
          });
        }
        return basis === "cpus_per_task" ? t("jobDetail.metricsCpuNormalization") : null;
      }).filter((note): note is string => note != null);
      notes.push(...Array.from(new Set(normalizationNotes)));
    }
  }
  if (missingScope) notes.push(t("jobDetail.metricsLegacyScope"));
  const nodes = Array.from(new Set(streams.map(stream => stream.labels.node).filter(Boolean)));
  if (nodes.length) notes.push(t("jobDetail.metricsSampledNodes", { nodes: nodes.join(", ") }));
  return notes;
}

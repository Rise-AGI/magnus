"use client";

import { useLanguage } from "@/context/language-context";
import { presentJobResources } from "@/lib/job-resources";
import type { Job } from "@/types/job";

export function JobResources({ job, expanded = false }: { job: Job; expanded?: boolean }) {
  const { t } = useLanguage();
  const { values } = presentJobResources(job, t);
  return (
    <div
      role="group"
      className={expanded ? "flex flex-wrap gap-x-4 gap-y-2" : "flex flex-col items-center gap-1 whitespace-normal"}
    >
      {values.map(value => (
        <span key={value} className={expanded ? "text-sm text-zinc-200 font-medium" : "text-xs text-zinc-300 font-medium"}>
          {value}
        </span>
      ))}
    </div>
  );
}

"use client";

import { useLanguage } from "@/context/language-context";
import { presentJobResources } from "@/lib/job-resources";
import type { Job } from "@/types/job";

export function JobResources({ job, expanded = false }: { job: Job; expanded?: boolean }) {
  const { t } = useLanguage();
  const { scope, values, details } = presentJobResources(job, t);
  const explanation = [scope, ...details].join("\n");
  return (
    <div
      role="group"
      className={expanded ? "space-y-3" : "flex flex-col items-center gap-1 whitespace-normal"}
      title={expanded ? undefined : explanation}
      aria-label={expanded ? undefined : [scope, ...values, ...details].join(". ")}
    >
      <span className="text-[10px] text-zinc-500 block">{scope}</span>
      <div className={expanded ? "flex flex-wrap gap-x-4 gap-y-2" : "flex flex-col items-center gap-1"}>
        {values.map(value => (
          <span key={value} className={expanded ? "text-sm text-zinc-200 font-medium" : "text-xs text-zinc-300 font-medium"}>
            {value}
          </span>
        ))}
      </div>
      {expanded && (
        <div className="space-y-1 text-xs text-zinc-500">
          {details.map(detail => <p key={detail}>{detail}</p>)}
        </div>
      )}
    </div>
  );
}

// front_end/src/components/jobs/job-table.tsx
"use client";

import { useRouter } from "next/navigation";
import { Box, Loader2, RefreshCw, SquareX } from "lucide-react";
import { Job } from "@/types/job";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { CopyableText } from "@/components/ui/copyable-text";
import { JobPriorityBadge } from "@/components/jobs/job-priority-badge";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { UserAvatar } from "@/components/ui/user-avatar";
import { formatBeijingTime } from "@/lib/utils";
import { cn } from "@/lib/utils";

interface JobTableProps {
  jobs: Job[];
  loading: boolean;
  onClone: (job: Job) => void;
  onTerminate: (job: Job) => void;
  emptyMessage?: string;
  className?: string;
  fromSource?: string;
}

export function JobTable({
  jobs,
  loading,
  onClone,
  onTerminate,
  emptyMessage,
  className = "min-h-[400px]",
  fromSource = "jobs",
}: JobTableProps) {
  const router = useRouter();
  const { user: currentUser } = useAuth();
  const { t } = useLanguage();

  if (loading) {
    return (
      <div className={cn("border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 gap-3", className)}>
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
        <p className="text-sm font-medium">{t("jobs.fetchingJobs")}</p>
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className={cn("border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500", className)}>
        <Box className="w-10 h-10 opacity-20 mb-3" />
        <p className="text-base font-medium text-zinc-400">{emptyMessage || t("jobs.noJobsFound")}</p>
      </div>
    );
  }

  return (
    <div className={cn("border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col overflow-hidden", className)}>
      <div className="overflow-x-auto w-full">
        <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
          <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
            <tr>
              <th className="px-6 py-4 font-medium w-[21%]">{t("jobs.table.task")}</th>
              <th className="px-6 py-4 font-medium w-[8%] text-center">{t("jobs.table.priority")}</th>
              <th className="px-6 py-4 font-medium w-[13%] text-center">{t("jobs.table.status")}</th>
              <th className="px-6 py-4 font-medium w-[20%] text-center">{t("jobs.table.repo")}</th>
              <th className="px-6 py-4 font-medium w-[13%] text-center">{t("jobs.table.resources")}</th>
              <th className="px-6 py-4 font-medium w-[15%]">{t("jobs.table.creator")}</th>
              <th className="px-6 py-4 font-medium text-right w-[10%]"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {jobs.map((job) => {
              const isActive = ["Pending", "Preparing", "Queued", "Running", "Paused"].includes(job.status);
              const isOwner = currentUser?.id === job.user?.id;
              const canTerminate = (isOwner || currentUser?.is_admin) && isActive;

              return (
                <tr
                  key={job.id}
                  onClick={() => {
                    const sel = window.getSelection();
                    if (sel && sel.toString().length > 0) return;
                    router.push(`/jobs/${job.id}?from=${fromSource}`);
                  }}
                  className="hover:bg-zinc-800/40 transition-colors group border-b border-zinc-800/50 last:border-0"
                >
                  <td className="px-6 py-4 align-top whitespace-normal break-all">
                    <div className="flex flex-col gap-1.5">
                      <div className="flex items-center gap-2">
                        <CopyableText
                          text={job.task_name}
                          variant="text"
                          className="font-semibold text-zinc-200 text-base"
                        />
                      </div>
                      <div className="flex items-center gap-2">
                        <CopyableText text={job.id} className="text-[10px] tracking-wider" />
                      </div>
                    </div>
                  </td>

                  <td className="px-6 py-4 align-top text-center">
                    <JobPriorityBadge type={job.job_type} />
                  </td>

                  <td className="px-6 py-4 align-top text-center">
                    <JobStatusBadge status={job.status} />
                  </td>

                  <td className="px-6 py-4 align-top">
                    <div className="flex flex-col gap-1.5 items-center">
                      <span className="text-zinc-300 flex items-center gap-2 text-xs font-medium bg-zinc-900/50 w-fit px-2 py-1 rounded border border-zinc-800">
                        <Box className="w-3.5 h-3.5 text-zinc-500" />
                        {job.namespace} / {job.repo_name}
                      </span>
                      <div className="flex items-center gap-2 text-xs text-zinc-500 font-mono ml-1">
                        <div className="w-1.5 h-1.5 rounded-full bg-zinc-600 flex-shrink-0"></div>
                        <span
                          className="truncate max-w-[80px] sm:max-w-[140px] xl:max-w-[200px]"
                          title={job.branch ?? "TBD"}
                        >
                          {job.branch ?? "TBD"}
                        </span>
                        <span className="text-zinc-700 flex-shrink-0">|</span>
                        <span className="bg-zinc-800 px-1.5 rounded text-zinc-400 flex-shrink-0">
                          {job.commit_sha ? job.commit_sha.substring(0, 7) : "TBD"}
                        </span>
                      </div>
                    </div>
                  </td>

                  <td className="px-6 py-4 align-top text-center">
                    <div className="flex flex-col items-center gap-1">
                      {job.cpu_count != null && (
                        <span className="text-zinc-300 text-xs font-medium">
                          CPU × {job.cpu_count}
                        </span>
                      )}
                      {job.memory_demand != null && (
                        <span className="text-zinc-300 text-xs font-medium">
                          RAM {job.memory_demand}
                        </span>
                      )}
                      {job.gpu_count > 0 && job.gpu_type !== "cpu" && (
                        <span className="text-zinc-300 text-xs font-medium">
                          {job.gpu_type.replace(/_/g, " ")} × {job.gpu_count}
                        </span>
                      )}
                      {!(job.gpu_count > 0 && job.gpu_type !== "cpu") && job.cpu_count == null && job.memory_demand == null && (
                        <span className="text-zinc-300 text-xs font-medium">{t("jobs.cpuOnly")}</span>
                      )}
                    </div>
                  </td>

                  <td className="px-6 py-4 align-top">
                    <div>
                      <UserAvatar
                        user={job.user}
                        subText={formatBeijingTime(job.created_at)}
                      />
                    </div>
                  </td>

                  <td className="px-6 py-4 align-middle text-right">
                    <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all transform translate-x-2 group-hover:translate-x-0">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onClone(job);
                        }}
                        className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                        title={t("jobs.cloneRerun")}
                      >
                        <RefreshCw className="w-4 h-4" />
                      </button>

                      {canTerminate && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onTerminate(job);
                          }}
                          className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                          title={t("jobs.terminateJob")}
                        >
                          <SquareX className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
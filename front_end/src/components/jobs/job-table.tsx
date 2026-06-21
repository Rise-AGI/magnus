// front_end/src/components/jobs/job-table.tsx
"use client";

import { useRouter } from "next/navigation";
import { Box, RefreshCw, SquareX } from "lucide-react";
import { Job } from "@/types/job";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { CopyableText } from "@/components/ui/copyable-text";
import { PageLoader } from "@/components/ui/page-loader";
import { JobPriorityBadge } from "@/components/jobs/job-priority-badge";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { UserAvatar } from "@/components/ui/user-avatar";
import { PersonHoverCard } from "@/components/ui/person-hover-card";
import { formatBeijingTime } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { useFromHref } from "@/hooks/use-from-href";

interface JobTableProps {
  jobs: Job[];
  loading: boolean;
  onClone: (job: Job) => void;
  onTerminate: (job: Job) => void;
  emptyMessage?: string;
  className?: string;
}

export function JobTable({
  jobs,
  loading,
  onClone,
  onTerminate,
  emptyMessage,
  className = "min-h-[400px]",
}: JobTableProps) {
  const router = useRouter();
  const { user: currentUser } = useAuth();
  const { t } = useLanguage();
  const isMobile = useIsMobile();
  const buildFromHref = useFromHref();

  if (loading) {
    return <PageLoader variant="card" label={t("jobs.fetchingJobs")} className={className} />;
  }

  if (jobs.length === 0) {
    return (
      <div className={cn("border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500", className)}>
        <Box className="w-10 h-10 opacity-20 mb-3" />
        <p className="text-base font-medium text-zinc-400">{emptyMessage || t("jobs.noJobsFound")}</p>
      </div>
    );
  }

  if (isMobile) {
    return (
      <div className={cn("space-y-3", className)}>
        {jobs.map((job) => {
          // inflight release (后端 JobResponse.is_releasing): scancel 已发但 SLURM
          // CG 还在收尾。这种状态下再次 terminate 在后端层 idempotent 但 UX 上
          // 误导用户以为 cancel 没生效，故 UI 隐藏终结按钮。
          const isActive = ["Pending", "Preparing", "Running", "Paused"].includes(job.status) && !job.is_releasing;
          const isOwner = currentUser?.id === job.user?.id;
          // External SLURM tasks are mock entries surfaced from the queue; Magnus has no
          // record of them and cannot clone or terminate them via /api/jobs/{id}.
          const isExternal = job.job_type === "N/A";
          const canTerminate = (isOwner || currentUser?.is_admin) && isActive && !isExternal;
          const canClone = !isExternal;

          return (
            <div
              key={job.id}
              onClick={() => router.push(buildFromHref(`/jobs/${job.id}`))}
              className="border border-zinc-800 rounded-xl bg-zinc-900/40 p-4 cursor-pointer active:bg-zinc-800/60 active:scale-[0.99] transition-all"
            >
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-zinc-200 text-sm truncate">{job.task_name}</p>
                  <CopyableText text={job.id} className="text-[10px] tracking-wider" />
                </div>
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  <JobPriorityBadge type={job.job_type} />
                  <JobStatusBadge status={job.status} isReleasing={job.is_releasing} />
                </div>
              </div>

              <div className="flex items-center justify-between mt-3">
                <div className="flex items-center gap-2 min-w-0">
                  {job.user ? (
                    <PersonHoverCard
                      userId={job.user.id}
                      warm={{ name: job.user.name, avatar_url: job.user.avatar_url ?? null }}
                    >
                      <span className="inline-flex">
                        <UserAvatar user={job.user} subText={formatBeijingTime(job.created_at)} />
                      </span>
                    </PersonHoverCard>
                  ) : (
                    <UserAvatar user={job.user} subText={formatBeijingTime(job.created_at)} />
                  )}
                </div>
                <div className="flex gap-2 flex-shrink-0">
                  {canClone && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onClone(job); }}
                      className="p-3 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-zinc-400 border border-zinc-700/50 active:scale-95"
                      title={t("jobs.cloneRerun")}
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                  )}
                  {canTerminate && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onTerminate(job); }}
                      className="p-3 bg-red-950/30 hover:bg-red-900/50 text-red-400 rounded-lg border border-red-900/30 active:scale-95"
                      title={t("jobs.terminateJob")}
                    >
                      <SquareX className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
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
              // inflight release (后端 JobResponse.is_releasing): scancel 已发但 SLURM
              // CG 还在收尾。这种状态下再次 terminate 在后端层 idempotent 但 UX 上
              // 误导用户以为 cancel 没生效，故 UI 隐藏终结按钮。
              const isActive = ["Pending", "Preparing", "Running", "Paused"].includes(job.status) && !job.is_releasing;
              const isOwner = currentUser?.id === job.user?.id;
              // External SLURM tasks are mock entries surfaced from the queue; Magnus has no
              // record of them and cannot clone or terminate them via /api/jobs/{id}.
              const isExternal = job.job_type === "N/A";
              const canTerminate = (isOwner || currentUser?.is_admin) && isActive && !isExternal;
              const canClone = !isExternal;

              return (
                <tr
                  key={job.id}
                  onClick={() => {
                    const sel = window.getSelection();
                    if (sel && sel.toString().length > 0) return;
                    router.push(buildFromHref(`/jobs/${job.id}`));
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
                    <JobStatusBadge status={job.status} isReleasing={job.is_releasing} />
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
                      {job.time_limit != null && (
                        <span className="text-zinc-300 text-xs font-medium">
                          {t("jobDetail.timeLimitValue", { value: job.time_limit.toString() })}
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

                  <td className="px-6 py-4 align-top whitespace-normal break-words">
                    <div>
                      {job.user ? (
                        <PersonHoverCard
                          userId={job.user.id}
                          warm={{ name: job.user.name, avatar_url: job.user.avatar_url ?? null }}
                        >
                          <span className="inline-flex">
                            <UserAvatar
                              user={job.user}
                              subText={formatBeijingTime(job.created_at)}
                            />
                          </span>
                        </PersonHoverCard>
                      ) : (
                        <UserAvatar
                          user={job.user}
                          subText={formatBeijingTime(job.created_at)}
                        />
                      )}
                    </div>
                  </td>

                  <td className="px-6 py-4 align-middle text-right">
                    <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all transform translate-x-2 group-hover:translate-x-0">
                      {canClone && (
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
                      )}

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
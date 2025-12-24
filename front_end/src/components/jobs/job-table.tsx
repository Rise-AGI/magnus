// src/components/jobs/job-table.tsx
"use client";

import { useRouter } from "next/navigation";
import { Box, Loader2, RefreshCw, SquareX } from "lucide-react";
import { Job } from "@/types/job";
import { useAuth } from "@/context/auth-context";
import { CopyableText } from "@/components/ui/copyable-text";
import { JobPriorityBadge } from "@/components/jobs/job-priority-badge";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { UserAvatar } from "@/components/ui/user-avatar";
import { formatBeijingTime } from "@/lib/utils";

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
  emptyMessage = "No jobs found",
  className = "min-h-[300px]" // 默认高度设为 300px，但可以被外部覆盖
}: JobTableProps) {
  const router = useRouter();
  const { user: currentUser } = useAuth();

  // --- Loading State ---
  if (loading) {
    return (
      <div className={`border border-zinc-800 rounded-xl bg-zinc-900/30 shadow-sm flex flex-col items-center justify-center text-zinc-500 gap-3 ${className}`}>
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
        <p className="text-sm font-medium">Fetching jobs...</p>
      </div>
    );
  }

  // --- Empty State ---
  if (jobs.length === 0) {
    return (
      <div className={`border border-zinc-800 rounded-xl bg-zinc-900/30 shadow-sm flex flex-col items-center justify-center text-zinc-500 ${className}`}>
        <Box className="w-10 h-10 opacity-20 mb-3" />
        <p className="text-base font-medium text-zinc-400">{emptyMessage}</p>
      </div>
    );
  }

  // --- Table Content ---
  return (
    <div className={`border border-zinc-800 rounded-xl bg-zinc-900/30 shadow-sm flex flex-col overflow-hidden ${className ? className.replace(/min-h-\[.*?\]/g, '') : ''}`}> 
      <div className="overflow-x-auto w-full">
        <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
          <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
            <tr>
              <th className="px-6 py-4 font-medium w-[21%]">Task / Task ID</th>
              <th className="px-6 py-4 font-medium w-[8%] text-center">Priority</th>
              <th className="px-6 py-4 font-medium w-[13%] text-center">Status</th>
              <th className="px-6 py-4 font-medium w-[20%] text-center">Github Repo / Branch · Commit</th>
              <th className="px-6 py-4 font-medium w-[13%] text-center">Resources</th>
              <th className="px-6 py-4 font-medium w-[15%] text-center">Creator / Created at</th>
              <th className="px-6 py-4 font-medium text-right w-[10%]"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {jobs.map((job) => {
              const isActive = ["Pending", "Running", "Paused"].includes(job.status);
              const isOwner = currentUser?.id === job.user?.id;
              const canTerminate = isOwner && isActive;

              return (
                <tr
                  key={job.id}
                  onClick={() => router.push(`/jobs/${job.id}`)}
                  className="hover:bg-zinc-800/40 transition-colors group border-b border-zinc-800/50 last:border-0"
                >
                  {/* Task Name & ID */}
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

                  {/* Priority */}
                  <td className="px-6 py-4 align-top text-center">
                    <JobPriorityBadge type={job.job_type} />
                  </td>

                  {/* Status */}
                  <td className="px-6 py-4 align-top text-center">
                    <JobStatusBadge status={job.status} />
                  </td>

                  {/* Github Info */}
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
                          title={job.branch}
                        >
                          {job.branch}
                        </span>
                        <span className="text-zinc-700 flex-shrink-0">|</span>
                        <span className="bg-zinc-800 px-1.5 rounded text-zinc-400 flex-shrink-0">
                          {job.commit_sha.substring(0, 7)}
                        </span>
                      </div>
                    </div>
                  </td>

                  {/* Resources */}
                  <td className="px-6 py-4 align-top text-center">
                    <span className="text-zinc-300 text-sm font-medium">
                      {job.gpu_type === "cpu"
                        ? "cpu only"
                        : `${job.gpu_type.replace(/_/g, " ")} × ${job.gpu_count}`}
                    </span>
                  </td>

                  {/* Creator */}
                  <td className="px-6 py-4 align-top">
                    <div className="flex justify-center">
                      <UserAvatar
                        user={job.user}
                        subText={formatBeijingTime(job.created_at)}
                      />
                    </div>
                  </td>

                  {/* Actions */}
                  <td className="px-6 py-4 align-middle text-right">
                    <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all transform translate-x-2 group-hover:translate-x-0">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onClone(job);
                        }}
                        className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                        title="Clone & Rerun"
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
                          title="Terminate Job"
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
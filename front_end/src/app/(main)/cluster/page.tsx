// front_end/src/app/(main)/cluster/page.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Activity, Server, Clock, Cpu, Box, RefreshCw, SquareX } from "lucide-react";
import { client } from "@/lib/api";
import { Job } from "@/types/job";
import { POLL_INTERVAL } from "@/lib/config";
import { useRouter } from "next/navigation";
import { UserAvatar } from "@/components/ui/user-avatar";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { JobPriorityBadge } from "@/components/jobs/job-priority-badge";
import { CopyableText } from "@/components/ui/copyable-text";
import { formatBeijingTime } from "@/lib/utils";
import { JobFormData } from "@/components/jobs/job-form";
import { JobDrawer } from "@/components/jobs/job-drawer";
import { useAuth } from "@/context/auth-context";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";

interface ClusterStats {
  resources: {
    node: string;
    gpu_model: string;
    total: number;
    free: number;
    used: number;
  };
  running_jobs: Job[];
  pending_jobs: Job[];
}

export default function ClusterPage() {
  const router = useRouter();
  const { user: currentUser } = useAuth(); // Get current user
  const [stats, setStats] = useState<ClusterStats | null>(null);
  const [loading, setLoading] = useState(true);

  // --- Drawer State Logic ---
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [drawerMode, setDrawerMode] = useState<"create" | "clone">("create");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [cloneData, setCloneData] = useState<JobFormData | null>(null);

  // --- Terminate Dialog State ---
  const [jobToTerminate, setJobToTerminate] = useState<Job | null>(null);
  const [isTerminating, setIsTerminating] = useState(false);

  const handleCloneJob = (job: Job) => {
    setDrawerMode("clone");
    setSelectedJobId(job.id);
    setCloneData({
        taskName: `${job.task_name}-copy`,
        description: job.description || "",
        namespace: job.namespace, 
        repoName: job.repo_name,
        branch: job.branch,
        commit_sha: job.commit_sha,
        entry_command: job.entry_command,
        gpu_count: job.gpu_count,
        gpu_type: job.gpu_type,
        job_type: job.job_type,
        cpu_count: job.cpu_count,
        memory_demand: job.memory_demand,
        runner: job.runner,
    });
    setIsDrawerOpen(true);
  };

  // 触发弹窗
  const onClickTerminate = (job: Job) => {
    setJobToTerminate(job);
  };

  // 执行终止
  const executeTermination = async () => {
    if (!jobToTerminate) return;
    setIsTerminating(true);
    try {
        await client(`/api/jobs/${jobToTerminate.id}/terminate`, { method: "POST" });
        fetchStats(); // Refresh immediately
        setJobToTerminate(null); // Close dialog
    } catch (e) {
        alert("Failed to terminate job");
        console.error(e);
    } finally {
        setIsTerminating(false);
    }
  };

  const fetchStats = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const data = await client("/api/cluster/stats");
      setStats(data);
    } catch (e) {
      console.error("Failed to fetch cluster stats", e);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStats();
    const interval = setInterval(() => fetchStats(true), POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchStats]);

  if (loading && !stats) {
    return <div className="p-8 text-zinc-500">Loading cluster status...</div>;
  }

  if (!stats) return null;

  // 复用表格渲染函数 (已更新为与 Jobs 页面完全一致)
  const renderJobTable = (jobs: Job[], emptyMessage: string) => {
    if (jobs.length === 0) {
      return (
        <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl min-h-[150px] flex flex-col items-center justify-center text-zinc-500">
          <Box className="w-8 h-8 opacity-40 mb-2" />
          <p>{emptyMessage}</p>
        </div>
      );
    }

    return (
      <div className="border border-zinc-800 rounded-xl bg-zinc-900/30 overflow-hidden shadow-sm">
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
                // 判断是否显示终止按钮：必须是任务所有者，且任务处于活跃状态
                const isOwner = currentUser?.id === job.user?.id;
                const isActive = ['Pending', 'Running', 'Paused'].includes(job.status);
                const canTerminate = isOwner && isActive;

                return (
                  <tr 
                    key={job.id} 
                    onClick={() => router.push(`/jobs/${job.id}`)}
                    className="hover:bg-zinc-800/40 transition-colors group"
                  >
                    <td className="px-6 py-4 align-top whitespace-normal break-all">
                      <div className="flex flex-col gap-1.5">
                        <div className="flex items-center gap-2">
                            <CopyableText text={job.task_name} variant="text" className="font-semibold text-zinc-200 text-base" />
                        </div>
                        <div className="flex items-center gap-2">
                          <CopyableText text={job.id} className="text-[10px] tracking-wider" />
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4 align-top text-center"><JobPriorityBadge type={job.job_type} /></td>
                    <td className="px-6 py-4 align-top text-center"><JobStatusBadge status={job.status} /></td>
                    <td className="px-6 py-4 align-top">
                        <div className="flex flex-col gap-1.5 items-center">
                            <span className="text-zinc-300 flex items-center gap-2 text-xs font-medium bg-zinc-900/50 w-fit px-2 py-1 rounded border border-zinc-800">
                              <Box className="w-3.5 h-3.5 text-zinc-500"/> {job.namespace} / {job.repo_name}
                            </span>
                            <div className="flex items-center gap-2 text-xs text-zinc-500 font-mono ml-1">
                              <div className="w-1.5 h-1.5 rounded-full bg-zinc-600 flex-shrink-0"></div>
                              <span className="truncate max-w-[80px] sm:max-w-[120px]" title={job.branch}>{job.branch}</span>
                              <span className="text-zinc-700">|</span>
                              <span className="bg-zinc-800 px-1.5 rounded text-zinc-400">{job.commit_sha.substring(0, 7)}</span>
                            </div>
                        </div>
                    </td>
                    <td className="px-6 py-4 align-top text-center">
                        <span className="text-zinc-300 text-sm font-medium">
                            {job.gpu_type === 'cpu' ? 'cpu only' : `${job.gpu_type.replace(/_/g, ' ')} × ${job.gpu_count}`}
                        </span>
                    </td>
                    <td className="px-6 py-4 align-top">
                      <div className="flex justify-center">
                          <UserAvatar user={job.user} subText={formatBeijingTime(job.created_at)} />
                      </div>
                    </td>
                    <td className="px-6 py-4 align-middle text-right">
                      <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all transform translate-x-2 group-hover:translate-x-0">
                        <button 
                            onClick={(e) => { e.stopPropagation(); handleCloneJob(job); }}
                            className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm" 
                            title="Clone & Rerun"
                        >
                          <RefreshCw className="w-4 h-4" />
                        </button>

                        {/* Terminate Button: Only for Owner & Active Jobs */}
                        {canTerminate && (
                          <button 
                              onClick={(e) => { 
                                e.stopPropagation(); 
                                onClickTerminate(job); 
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
  };

  return (
    <div className="pb-20 relative">
      <style jsx global>{`
        ::-webkit-scrollbar {
          display: none;
        }
        html {
          -ms-overflow-style: none;
          scrollbar-width: none;
        }
      `}</style>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">Cluster Status</h1>
        <p className="text-zinc-500 text-sm mt-1">Real-time resource monitoring and queue status.</p>
      </div>

      {/* Resource Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-10">
        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm relative overflow-hidden group">
          <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity"><Cpu className="w-24 h-24 text-emerald-500" /></div>
          <div className="relative z-10">
            <div className="flex items-center gap-2 text-emerald-400 mb-2"><Activity className="w-4 h-4" /><span className="text-sm font-bold uppercase tracking-wider">Available GPUs</span></div>
            <div className="flex items-baseline gap-2"><span className="text-4xl font-bold text-white">{stats.resources.free}</span><span className="text-zinc-500 text-sm">/ {stats.resources.total}</span></div>
            <div className="mt-3 flex items-center gap-2 text-xs text-zinc-400 font-mono bg-zinc-800/50 w-fit px-2 py-1 rounded"><Server className="w-3 h-3" />{stats.resources.node} · {stats.resources.gpu_model}</div>
          </div>
        </div>
        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm">
          <div className="flex items-center gap-2 text-blue-400 mb-2"><Activity className="w-4 h-4" /><span className="text-sm font-bold uppercase tracking-wider">Active Jobs</span></div>
          <div className="text-4xl font-bold text-white">{stats.running_jobs.length}</div>
          <p className="text-zinc-500 text-xs mt-2">Currently executing on cluster</p>
        </div>
        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm">
          <div className="flex items-center gap-2 text-amber-400 mb-2"><Clock className="w-4 h-4" /><span className="text-sm font-bold uppercase tracking-wider">Queue Depth</span></div>
          <div className="text-4xl font-bold text-white">{stats.pending_jobs.length}</div>
          <p className="text-zinc-500 text-xs mt-2">Jobs waiting for resources</p>
        </div>
      </div>

      <div className="flex flex-col gap-10">
        <div className="flex flex-col gap-4">
          <h2 className="text-lg font-bold text-white flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>Running Jobs</h2>
          {renderJobTable(stats.running_jobs, "No active jobs")}
        </div>
        <div className="flex flex-col gap-4">
           <h2 className="text-lg font-bold text-white flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-amber-500"></span>Queued Jobs</h2>
          {renderJobTable(stats.pending_jobs, "Queue is empty")}
        </div>
      </div>

      <JobDrawer 
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        onSuccess={() => {
            setIsDrawerOpen(false);
            fetchStats();
        }}
        mode={drawerMode}
        initialData={cloneData}
        formKey={drawerMode + (selectedJobId || "")}
      />

      {/* --- Confirmation Dialog --- */}
      <ConfirmationDialog
        isOpen={!!jobToTerminate}
        onClose={() => setJobToTerminate(null)}
        onConfirm={executeTermination}
        title="Terminate Task?"
        description={
          <span>
            Are you sure you want to terminate <strong>{jobToTerminate?.task_name}</strong>? 
            <br />
            This action will stop the process immediately and cannot be undone.
          </span>
        }
        confirmText="Terminate"
        variant="danger"
        isLoading={isTerminating}
      />
    </div>
  );
}
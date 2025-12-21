// front_end/src/app/(main)/dashboard/page.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Zap, RefreshCw, Box, SquareX, Plus, Loader2, Activity, Cpu, Clock, CalendarDays } from "lucide-react"; 
import { client } from "@/lib/api";
import { Job } from "@/types/job";
import { POLL_INTERVAL } from "@/lib/config";
import { useRouter } from "next/navigation";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { JobPriorityBadge } from "@/components/jobs/job-priority-badge";
import { CopyableText } from "@/components/ui/copyable-text";
import { formatBeijingTime } from "@/lib/utils";
import { JobDrawer } from "@/components/jobs/job-drawer";
import { JobFormData } from "@/components/jobs/job-form";
import { useAuth } from "@/context/auth-context";
import { UserAvatar } from "@/components/ui/user-avatar";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";

interface DashboardStats {
  total_occupancy_24h: number;
  total_occupancy_7d: number;
  magnus_utilization_24h: number;
  magnus_utilization_7d: number;
}

function StatCard({ 
  label, 
  value, 
  subLabel, 
  icon: Icon, 
  colorClass 
}: { 
  label: string; 
  value: number | null; 
  subLabel: string; 
  icon: any; 
  colorClass: string 
}) {
  return (
    <div className="bg-zinc-900/50 border border-zinc-800 rounded-xl p-5 flex items-start justify-between shadow-sm backdrop-blur-sm hover:border-zinc-700 transition-colors">
      <div>
        <p className="text-zinc-500 text-xs font-medium uppercase tracking-wider mb-1">{label}</p>
        <div className="flex items-baseline gap-2">
          {value === null ? (
            <div className="h-8 w-16 bg-zinc-800 animate-pulse rounded my-1" />
          ) : (
            <h3 className="text-2xl font-bold text-zinc-100">{(value * 100).toFixed(1)}%</h3>
          )}
        </div>
        <p className="text-zinc-500 text-xs mt-1">{subLabel}</p>
      </div>
      <div className={`p-2 rounded-lg ${colorClass} bg-opacity-10`}>
        <Icon className={`w-5 h-5 ${colorClass.replace("bg-", "text-")}`} />
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const { user: currentUser } = useAuth();
  
  const [activeJobs, setActiveJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  // Drawer State
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [drawerMode, setDrawerMode] = useState<"create" | "clone">("create");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [cloneData, setCloneData] = useState<JobFormData | null>(null);

  // Terminate Dialog State
  const [jobToTerminate, setJobToTerminate] = useState<Job | null>(null);
  const [isTerminating, setIsTerminating] = useState(false);

  const fetchDashboardData = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      // Parallel fetch with error handling for stats to avoid blocking the main list
      const [jobsData, statsData] = await Promise.all([
        client("/api/dashboard/my-active-jobs"),
        client("/api/dashboard/stats").catch(e => {
            console.warn("Failed to fetch stats", e);
            return null;
        })
      ]);
      
      setActiveJobs(jobsData);
      if (statsData) setStats(statsData);

    } catch (e) {
      console.error("Failed to fetch dashboard data", e);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDashboardData();
    const interval = setInterval(() => fetchDashboardData(true), POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchDashboardData]);

  const handleNewJob = () => {
    setDrawerMode("create");
    setCloneData(null);
    setSelectedJobId(null);
    setIsDrawerOpen(true);
  };

  const handleCloneJob = (job: Job) => {
    setDrawerMode("clone");
    setSelectedJobId(job.id);
    setCloneData({
        taskName: `${job.task_name}`,
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

  const onClickTerminate = (job: Job) => {
    setJobToTerminate(job);
  };

  const executeTermination = async () => {
    if (!jobToTerminate) return;
    setIsTerminating(true);
    try {
        await client(`/api/jobs/${jobToTerminate.id}/terminate`, { method: "POST" });
        fetchDashboardData(true); 
        setJobToTerminate(null);
    } catch (e) {
        alert("Failed to terminate job");
        console.error(e);
    } finally {
        setIsTerminating(false);
    }
  };

  return (
    <div className="pb-20 relative min-h-[calc(100vh-8rem)]">

      <style jsx global>{`
        ::-webkit-scrollbar {
          display: none;
        }
        html {
          -ms-overflow-style: none;
          scrollbar-width: none;
        }
      `}</style>

      <div className="mb-8 flex items-center justify-between">
        <div>
            <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            Dashboard
            </h1>
            <p className="text-zinc-500 text-sm mt-1">Welcome back, {currentUser?.name}. Here is your workload overview.</p>
        </div>
        <button 
          onClick={handleNewJob} 
          className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50"
        >
          <Plus className="w-4 h-4" /> New Job
        </button>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard 
            label="Total Occupancy (24h)" 
            value={stats?.total_occupancy_24h ?? null}
            subLabel="All Slurm Tasks"
            icon={Activity}
            colorClass="bg-emerald-500 text-emerald-500"
        />
        <StatCard 
            label="Total Occupancy (7d)" 
            value={stats?.total_occupancy_7d ?? null}
            subLabel="All Slurm Tasks"
            icon={CalendarDays}
            colorClass="bg-teal-500 text-teal-500"
        />
        <StatCard 
            label="Magnus Utilization (24h)" 
            value={0.0}
            subLabel="Platform Managed（施工中）"
            icon={Cpu}
            colorClass="bg-blue-500 text-blue-500"
        />
        <StatCard 
            label="Magnus Utilization (7d)" 
            value={0.0}
            subLabel="Platform Managed（施工中）"
            icon={Clock}
            colorClass="bg-indigo-500 text-indigo-500"
        />
      </div>

      <div className="flex flex-col gap-6">
        <div className="flex items-center gap-2 text-zinc-200 font-semibold text-lg">
            <Zap className="w-5 h-5 text-yellow-500 fill-yellow-500/20" />
            My Active Tasks
        </div>

        <div className="border border-zinc-800 rounded-xl bg-zinc-900/30 overflow-hidden shadow-sm min-h-[200px]">
            {loading && activeJobs.length === 0 ? (
                 <div className="flex flex-col items-center justify-center p-12 text-zinc-500">
                    <Loader2 className="w-8 h-8 animate-spin text-blue-500 mb-3" />
                    <p className="text-sm font-medium">Loading...</p>
                 </div>
            ) : activeJobs.length === 0 ? (
                <div className="flex flex-col items-center justify-center p-12 text-zinc-500">
                    <Box className="w-12 h-12 opacity-20 mb-3" />
                    <p>No active jobs found.</p>
                    <p className="text-xs mt-1">Jobs marked as Success, Failed, or Terminated are not shown here.</p>
                </div>
            ) : (
                <div className="overflow-x-auto w-full">
                <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
                    <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
                    <tr>
                        <th className="px-6 py-4 font-medium w-[25%]">Task / Task ID</th>
                        <th className="px-6 py-4 font-medium w-[10%] text-center">Priority</th>
                        <th className="px-6 py-4 font-medium w-[15%] text-center">Status</th>
                        <th className="px-6 py-4 font-medium w-[15%] text-center">Resources</th>
                        <th className="px-6 py-4 font-medium w-[15%] text-center">Creator / Created at</th>
                        <th className="px-6 py-4 font-medium text-right w-[20%]">Actions</th>
                    </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-800/50">
                    {activeJobs.map((job) => (
                        <tr 
                        key={job.id} 
                        onClick={() => router.push(`/jobs/${job.id}`)}
                        className="hover:bg-zinc-800/40 transition-colors group cursor-pointer"
                        >
                        <td className="px-6 py-4 align-top whitespace-normal break-all">
                            <div className="flex flex-col gap-1.5">
                                <span className="font-semibold text-zinc-200 text-base">{job.task_name}</span>
                                <CopyableText text={job.id} className="text-[10px] tracking-wider" />
                            </div>
                        </td>
                        <td className="px-6 py-4 align-top text-center">
                            <JobPriorityBadge type={job.job_type} />
                        </td>
                        <td className="px-6 py-4 align-top text-center">
                            <JobStatusBadge status={job.status} />
                        </td>
                        <td className="px-6 py-4 align-top text-center">
                             <span className="text-zinc-300 text-sm font-medium">
                                {job.gpu_type === 'cpu' ? 'cpu only' : `${job.gpu_type.replace(/_/g, ' ')} × ${job.gpu_count}`}
                             </span>
                        </td>
                        <td className="px-6 py-4 align-top">
                             <div className="flex justify-center">
                                <UserAvatar 
                                    user={job.user} 
                                    subText={formatBeijingTime(job.created_at)} 
                                />
                             </div>
                        </td>
                        <td className="px-6 py-4 align-middle text-right">
                            <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all">
                                <button 
                                    onClick={(e) => { e.stopPropagation(); handleCloneJob(job); }}
                                    className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50" 
                                    title="Clone"
                                >
                                    <RefreshCw className="w-4 h-4" />
                                </button>
                                
                                {currentUser?.id === job.user?.id && (
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
                    ))}
                    </tbody>
                </table>
                </div>
            )}
        </div>
      </div>

      <JobDrawer
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        onSuccess={() => {
            setIsDrawerOpen(false);
            fetchDashboardData();
        }}
        mode={drawerMode}
        initialData={cloneData}
        formKey={drawerMode + (selectedJobId || "")}
      />

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
// front_end/src/app/(main)/cluster/page.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Activity, Server, Clock, Cpu } from "lucide-react";
import { client } from "@/lib/api";
import { Job } from "@/types/job";
import { POLL_INTERVAL } from "@/lib/config";
import { JobDrawer } from "@/components/jobs/job-drawer";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { useJobOperations } from "@/hooks/use-job-operations";
import { JobTable } from "@/components/jobs/job-table";
import { PaginationControls } from "@/components/ui/pagination-controls";

interface ClusterStats {
  resources: {
    node: string;
    gpu_model: string;
    total: number;
    free: number;
    used: number;
  };
  running_jobs: Job[];
  total_running: number;
  pending_jobs: Job[];
  total_pending: number;
}

export default function ClusterPage() {
  const [stats, setStats] = useState<ClusterStats | null>(null);
  const [loading, setLoading] = useState(true);

  // Running Jobs Pagination State
  const [runningPage, setRunningPage] = useState(1);
  const [runningSize, setRunningSize] = useState(5);

  // Pending Jobs Pagination State
  const [pendingPage, setPendingPage] = useState(1);
  const [pendingSize, setPendingSize] = useState(5);

  const fetchStats = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      // 构造两组分页参数
      const params = new URLSearchParams({
        running_skip: ((runningPage - 1) * runningSize).toString(),
        running_limit: runningSize.toString(),
        pending_skip: ((pendingPage - 1) * pendingSize).toString(),
        pending_limit: pendingSize.toString(),
      });

      const data = await client(`/api/cluster/stats?${params.toString()}`);
      setStats(data);
    } catch (e) {
      console.error("Failed to fetch cluster stats", e);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [runningPage, runningSize, pendingPage, pendingSize]);

  const { 
    drawerProps, 
    terminateDialogProps, 
    handleCloneJob, 
    onClickTerminate 
  } = useJobOperations({ onSuccess: fetchStats });

  useEffect(() => {
    fetchStats();
    const interval = setInterval(() => fetchStats(true), POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchStats]);

  // 当 loading 且没有任何数据时显示 Loading 状态
  // (如果已经有 stats 数据但在做后台刷新，则保留显示旧数据)
  if (loading && !stats) {
    return <div className="p-8 text-zinc-500">Loading cluster status...</div>;
  }

  // 安全检查，防止 stats 为空时渲染
  if (!stats) return null;

  return (
    <div className="pb-20 relative">
      <style jsx global>{`
        ::-webkit-scrollbar { display: none; }
        html { -ms-overflow-style: none; scrollbar-width: none; }
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
          {/* 这里显示 Total Running 而不是当前页的长度 */}
          <div className="text-4xl font-bold text-white">{stats.total_running}</div>
          <p className="text-zinc-500 text-xs mt-2">Currently executing on cluster</p>
        </div>
        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm">
          <div className="flex items-center gap-2 text-amber-400 mb-2"><Clock className="w-4 h-4" /><span className="text-sm font-bold uppercase tracking-wider">Queue Depth</span></div>
          {/* 这里显示 Total Pending */}
          <div className="text-4xl font-bold text-white">{stats.total_pending}</div>
          <p className="text-zinc-500 text-xs mt-2">Jobs waiting for resources</p>
        </div>
      </div>

      <div className="flex flex-col gap-10">
        
        {/* === Running Jobs Section === */}
        <div className="flex flex-col gap-4">
          <h2 className="text-lg font-bold text-white flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>Running Jobs</h2>
          
          <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <JobTable 
              jobs={stats.running_jobs}
              loading={false}
              onClone={handleCloneJob}
              onTerminate={onClickTerminate}
              emptyMessage="No running jobs."
              className="border-none -h-[175px]"
            />
            
            {stats.total_running > 0 && (
              <div className="px-4 pb-2 bg-zinc-900/30">
                <PaginationControls 
                  currentPage={runningPage}
                  totalPages={Math.ceil(stats.total_running / runningSize)}
                  pageSize={runningSize}
                  totalItems={stats.total_running}
                  onPageChange={setRunningPage}
                  onPageSizeChange={(newSize) => {
                    setRunningSize(newSize);
                    setRunningPage(1);
                  }}
                  pageSizeOptions={[5, 10, 20]}
                />
              </div>
            )}
          </div>
        </div>

        {/* === Pending Jobs Section === */}
        <div className="flex flex-col gap-4">
          <h2 className="text-lg font-bold text-white flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-amber-500"></span>Queued Jobs</h2>
          
          <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <JobTable 
              jobs={stats.pending_jobs}
              loading={false}
              onClone={handleCloneJob}
              onTerminate={onClickTerminate}
              emptyMessage="Queue is empty."
              className="border-none min-h-[175px]"
            />
            
            {stats.total_pending > 0 && (
              <div className="px-4 pb-2 bg-zinc-900/30">
                <PaginationControls 
                  currentPage={pendingPage}
                  totalPages={Math.ceil(stats.total_pending / pendingSize)}
                  pageSize={pendingSize}
                  totalItems={stats.total_pending}
                  onPageChange={setPendingPage}
                  onPageSizeChange={(newSize) => {
                    setPendingSize(newSize);
                    setPendingPage(1);
                  }}
                  pageSizeOptions={[5, 10, 20]}
                />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Dialogs */}
      <JobDrawer {...drawerProps} />
      <ConfirmationDialog {...terminateDialogProps} />
    </div>
  );
}
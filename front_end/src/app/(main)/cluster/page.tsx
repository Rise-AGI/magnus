// front_end/src/app/(main)/cluster/page.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Activity, Server, Clock, Cpu } from "lucide-react";
import { client } from "@/lib/api";
import { Job } from "@/types/job";
import { POLL_INTERVAL } from "@/lib/config";
import { useLanguage } from "@/context/language-context";
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
    cpu_total: number;
    cpu_free: number;
    mem_total_mb: number;
    mem_free_mb: number;
  };
  running_jobs: Job[];
  total_running: number;
  pending_jobs: Job[];
  total_pending: number;
}

export default function ClusterPage() {
  const { t } = useLanguage();
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
    errorDialogProps,
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
    return <div className="p-8 text-zinc-500">{t("cluster.loadingStatus")}</div>;
  }

  // 安全检查，防止 stats 为空时渲染
  if (!stats) return null;

  const formatMem = (mb: number) => mb >= 1024 ? `${(mb / 1024).toFixed(0)} GB` : `${mb} MB`;

  return (
    <div className="pb-20 relative">
      <style jsx global>{`
        ::-webkit-scrollbar { display: none; }
        html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>
      
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">Cluster</h1>
        <p className="text-zinc-500 text-sm mt-1">{t("cluster.subtitle")}</p>
      </div>

      {/* Resource Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-10">
        {/* Card 1: CPU / Mem */}
        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm flex flex-col justify-between h-full">
          <div className="flex items-center gap-2 text-cyan-400 mb-2">
            <Cpu className="w-4 h-4" />
            <span className="text-sm font-bold uppercase tracking-wider">{t("cluster.availableCpuMem")}</span>
          </div>
          <div className="flex items-baseline gap-1.5 mb-2">
            <span className="text-3xl font-bold text-white">{stats.resources.cpu_free}</span>
            <span className="text-zinc-500 text-sm">/ {stats.resources.cpu_total} {t("cluster.cores")}</span>
          </div>
          <div className="flex items-center gap-1.5 text-zinc-400 text-xs">
            <span className="font-mono">RAM:</span>
            <span>{formatMem(stats.resources.mem_free_mb)} / {formatMem(stats.resources.mem_total_mb)}</span>
          </div>
        </div>

        {/* Card 2: GPU */}
        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm flex flex-col justify-between h-full">
          <div className="flex items-center gap-2 text-emerald-400 mb-2">
            <Activity className="w-4 h-4" />
            <span className="text-sm font-bold uppercase tracking-wider">{t("cluster.availableGpus")}</span>
          </div>
          <div className="flex items-baseline gap-2 mb-2">
            <span className="text-3xl font-bold text-white">{stats.resources.free}</span>
            <span className="text-zinc-500 text-sm">/ {stats.resources.total}</span>
          </div>
          <div className="flex items-center gap-1.5 text-zinc-400 text-xs font-mono">
            <Server className="w-3 h-3" />
            {stats.resources.gpu_model}
          </div>
        </div>

        {/* Card 3: Active Jobs */}
        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm flex flex-col justify-between h-full">
          <div className="flex items-center gap-2 text-blue-400 mb-2">
            <Activity className="w-4 h-4" />
            <span className="text-sm font-bold uppercase tracking-wider">{t("cluster.activeJobs")}</span>
          </div>
          <div className="text-3xl font-bold text-white mb-2">{stats.total_running}</div>
          <div className="text-zinc-400 text-xs">
            {t("cluster.activeJobsDesc")}
          </div>
        </div>

        {/* Card 4: Queue Depth */}
        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm flex flex-col justify-between h-full">
          <div className="flex items-center gap-2 text-amber-400 mb-2">
            <Clock className="w-4 h-4" />
            <span className="text-sm font-bold uppercase tracking-wider">{t("cluster.queueDepth")}</span>
          </div>
          <div className="text-3xl font-bold text-white mb-2">{stats.total_pending}</div>
          <div className="text-zinc-400 text-xs">
            {t("cluster.queueDepthDesc")}
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-10">

        {/* === Running Jobs Section === */}
        <div className="flex flex-col gap-4">
          <h2 className="text-lg font-bold text-white flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>{t("cluster.runningJobs")}</h2>

          <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <JobTable
              jobs={stats.running_jobs}
              loading={false}
              onClone={handleCloneJob}
              onTerminate={onClickTerminate}
              emptyMessage={t("cluster.noRunningJobs")}
              className="border-none min-h-[175px]"
              fromSource="cluster"
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
          <h2 className="text-lg font-bold text-white flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-amber-500"></span>{t("cluster.queuedJobs")}</h2>

          <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <JobTable
              jobs={stats.pending_jobs}
              loading={false}
              onClone={handleCloneJob}
              onTerminate={onClickTerminate}
              emptyMessage={t("cluster.queueEmpty")}
              className="border-none min-h-[175px]"
              fromSource="cluster"
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
      <ConfirmationDialog {...errorDialogProps} />
    </div>
  );
}
// front_end/src/app/(main)/cluster/page.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Zap, Plus, Activity, Cpu, Clock, Server } from "lucide-react";
import { client } from "@/lib/api";
import { Job } from "@/types/job";
import { POLL_INTERVAL } from "@/lib/config";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { JobDrawer } from "@/components/jobs/job-drawer";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { useJobOperations } from "@/hooks/use-job-operations";
import { JobTable } from "@/components/jobs/job-table";
import { PaginationControls } from "@/components/ui/pagination-controls";

interface ClusterResources {
  node: string;
  gpu_model: string;
  total: number;
  free: number;
  used: number;
  cpu_total: number;
  cpu_free: number;
  mem_total_mb: number;
  mem_free_mb: number;
}

interface ClusterStats {
  resources: ClusterResources;
  running_jobs: Job[];
  total_running: number;
  pending_jobs: Job[];
  total_pending: number;
}

export default function ClusterPage() {
  const { user: currentUser } = useAuth();
  const { t } = useLanguage();

  const [cluster, setCluster] = useState<ClusterStats | null>(null);
  const [activeJobs, setActiveJobs] = useState<Job[]>([]);
  const [totalMyJobs, setTotalMyJobs] = useState(0);
  const [loading, setLoading] = useState(true);

  // My Active Jobs pagination
  const [myPage, setMyPage] = useState(1);
  const [mySize, setMySize] = useState(5);

  // Cluster running/pending pagination
  const [runningPage, setRunningPage] = useState(1);
  const [runningSize, setRunningSize] = useState(5);
  const [pendingPage, setPendingPage] = useState(1);
  const [pendingSize, setPendingSize] = useState(5);

  const fetchData = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const myParams = new URLSearchParams({
        skip: ((myPage - 1) * mySize).toString(),
        limit: mySize.toString(),
      });
      const clusterParams = new URLSearchParams({
        running_skip: ((runningPage - 1) * runningSize).toString(),
        running_limit: runningSize.toString(),
        pending_skip: ((pendingPage - 1) * pendingSize).toString(),
        pending_limit: pendingSize.toString(),
      });

      const [myJobsData, clusterData] = await Promise.all([
        client(`/api/cluster/my-active-jobs?${myParams.toString()}`),
        client(`/api/cluster/stats?${clusterParams.toString()}`).catch(e => {
          console.warn("Failed to fetch cluster stats", e);
          return null;
        }),
      ]);

      if (myJobsData && myJobsData.items) {
        setActiveJobs(myJobsData.items);
        setTotalMyJobs(myJobsData.total);
      } else if (Array.isArray(myJobsData)) {
        setActiveJobs(myJobsData);
        setTotalMyJobs(myJobsData.length);
      }

      if (clusterData) setCluster(clusterData);
    } catch (e) {
      console.error("Failed to fetch cluster data", e);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [myPage, mySize, runningPage, runningSize, pendingPage, pendingSize]);

  const {
    drawerProps,
    terminateDialogProps,
    errorDialogProps,
    handleNewJob,
    handleCloneJob,
    onClickTerminate
  } = useJobOperations({ onSuccess: fetchData });

  useEffect(() => {
    fetchData();
    const interval = setInterval(() => fetchData(true), POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchData]);

  const formatMem = (mb: number) => mb >= 1024 ? `${(mb / 1024).toFixed(0)} GB` : `${mb} MB`;

  return (
    <div className="pb-20 relative min-h-[calc(100vh-8rem)]">
      <style jsx global>{`
        ::-webkit-scrollbar { display: none; }
        html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">{t("nav.cluster")}</h1>
          <p className="text-zinc-500 text-sm mt-1">{t("cluster.welcome", { name: currentUser?.name || "" })}</p>
        </div>
        <button
          onClick={handleNewJob}
          className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50"
        >
          <Plus className="w-4 h-4" /> {t("cluster.newJob")}
        </button>
      </div>

      {/* Cluster Resource Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm flex flex-col justify-between h-full">
          <div className="flex items-center gap-2 text-cyan-400 mb-2">
            <Cpu className="w-4 h-4" />
            <span className="text-sm font-bold uppercase tracking-wider">{t("cluster.availableCpuMem")}</span>
          </div>
          {cluster ? (
            <>
              <div className="flex items-baseline gap-1.5 mb-2">
                <span className="text-3xl font-bold text-white">{cluster.resources.cpu_free}</span>
                <span className="text-zinc-500 text-sm">/ {cluster.resources.cpu_total} {t("cluster.cores")}</span>
              </div>
              <div className="flex items-center gap-1.5 text-zinc-400 text-xs">
                <span className="font-mono">RAM:</span>
                <span>{formatMem(cluster.resources.mem_free_mb)} / {formatMem(cluster.resources.mem_total_mb)}</span>
              </div>
            </>
          ) : (
            <div className="h-12 bg-zinc-800 animate-pulse rounded my-1" />
          )}
        </div>

        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm flex flex-col justify-between h-full">
          <div className="flex items-center gap-2 text-emerald-400 mb-2">
            <Activity className="w-4 h-4" />
            <span className="text-sm font-bold uppercase tracking-wider">{t("cluster.availableGpus")}</span>
          </div>
          {cluster ? (
            <>
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-3xl font-bold text-white">{cluster.resources.free}</span>
                <span className="text-zinc-500 text-sm">/ {cluster.resources.total}</span>
              </div>
              <div className="flex items-center gap-1.5 text-zinc-400 text-xs font-mono">
                <Server className="w-3 h-3" />
                {cluster.resources.gpu_model}
              </div>
            </>
          ) : (
            <div className="h-12 bg-zinc-800 animate-pulse rounded my-1" />
          )}
        </div>

        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm flex flex-col justify-between h-full">
          <div className="flex items-center gap-2 text-blue-400 mb-2">
            <Activity className="w-4 h-4" />
            <span className="text-sm font-bold uppercase tracking-wider">{t("cluster.activeJobs")}</span>
          </div>
          {cluster ? (
            <>
              <div className="text-3xl font-bold text-white mb-2">{cluster.total_running}</div>
              <div className="text-zinc-400 text-xs">{t("cluster.activeJobsDesc")}</div>
            </>
          ) : (
            <div className="h-12 bg-zinc-800 animate-pulse rounded my-1" />
          )}
        </div>

        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm flex flex-col justify-between h-full">
          <div className="flex items-center gap-2 text-amber-400 mb-2">
            <Clock className="w-4 h-4" />
            <span className="text-sm font-bold uppercase tracking-wider">{t("cluster.queueDepth")}</span>
          </div>
          {cluster ? (
            <>
              <div className="text-3xl font-bold text-white mb-2">{cluster.total_pending}</div>
              <div className="text-zinc-400 text-xs">{t("cluster.queueDepthDesc")}</div>
            </>
          ) : (
            <div className="h-12 bg-zinc-800 animate-pulse rounded my-1" />
          )}
        </div>
      </div>

      <div className="flex flex-col gap-10">
        {/* My Active Jobs */}
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-2 text-zinc-200 font-semibold text-lg">
            <Zap className="w-5 h-5 text-yellow-500 fill-yellow-500/20" />
            {t("cluster.myActiveJobs")}
          </div>

          <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <JobTable
              jobs={activeJobs}
              loading={loading && activeJobs.length === 0}
              onClone={handleCloneJob}
              onTerminate={onClickTerminate}
              emptyMessage={t("cluster.noActiveJobs")}
              className="border-none min-h-[200px]"
              fromSource="/cluster"
            />
            {totalMyJobs > 0 && (
              <div className="px-4 pb-2 bg-zinc-900/30">
                <PaginationControls
                  currentPage={myPage}
                  totalPages={Math.ceil(totalMyJobs / mySize)}
                  pageSize={mySize}
                  totalItems={totalMyJobs}
                  onPageChange={setMyPage}
                  onPageSizeChange={(s) => { setMySize(s); setMyPage(1); }}
                  pageSizeOptions={[5, 10, 20]}
                />
              </div>
            )}
          </div>
        </div>

        {/* Running Jobs (Cluster) */}
        <div className="flex flex-col gap-4">
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            {t("cluster.runningJobs")}
          </h2>
          <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <JobTable
              jobs={cluster?.running_jobs || []}
              loading={loading && !cluster}
              onClone={handleCloneJob}
              onTerminate={onClickTerminate}
              emptyMessage={t("cluster.noRunningJobs")}
              className="border-none min-h-[175px]"
              fromSource="/cluster"
            />
            {(cluster?.total_running || 0) > 0 && (
              <div className="px-4 pb-2 bg-zinc-900/30">
                <PaginationControls
                  currentPage={runningPage}
                  totalPages={Math.ceil((cluster?.total_running || 0) / runningSize)}
                  pageSize={runningSize}
                  totalItems={cluster?.total_running || 0}
                  onPageChange={setRunningPage}
                  onPageSizeChange={(s) => { setRunningSize(s); setRunningPage(1); }}
                  pageSizeOptions={[5, 10, 20]}
                />
              </div>
            )}
          </div>
        </div>

        {/* Queued Jobs (Cluster) */}
        <div className="flex flex-col gap-4">
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-amber-500"></span>
            {t("cluster.queuedJobs")}
          </h2>
          <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <JobTable
              jobs={cluster?.pending_jobs || []}
              loading={loading && !cluster}
              onClone={handleCloneJob}
              onTerminate={onClickTerminate}
              emptyMessage={t("cluster.queueEmpty")}
              className="border-none min-h-[175px]"
              fromSource="/cluster"
            />
            {(cluster?.total_pending || 0) > 0 && (
              <div className="px-4 pb-2 bg-zinc-900/30">
                <PaginationControls
                  currentPage={pendingPage}
                  totalPages={Math.ceil((cluster?.total_pending || 0) / pendingSize)}
                  pageSize={pendingSize}
                  totalItems={cluster?.total_pending || 0}
                  onPageChange={setPendingPage}
                  onPageSizeChange={(s) => { setPendingSize(s); setPendingPage(1); }}
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

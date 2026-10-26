// front_end/src/app/(main)/cluster/page.tsx
"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Zap, Plus, Activity, Cpu, Clock, Server } from "lucide-react";
import { client } from "@/lib/api";
import { Job } from "@/types/job";
import { POLL_INTERVAL, EXECUTION_TARGETS, DEFAULT_EXECUTION_TARGET } from "@/lib/config";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { JobDrawer } from "@/components/jobs/job-drawer";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { useJobOperations } from "@/hooks/use-job-operations";
import { usePolling } from "@/hooks/use-polling";
import { JobTable } from "@/components/jobs/job-table";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { useUrlPagination } from "@/hooks/use-url-pagination";
import { presentClusterResources, type ClusterResources } from "@/lib/cluster-resources";
import { useSearchParams } from "next/navigation";
import { ExecutionTargetSelect } from "@/components/jobs/execution-target-select";
import { cn } from "@/lib/utils";

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
  const searchParams = useSearchParams();
  const executionTarget = searchParams.get("execution_target") ?? DEFAULT_EXECUTION_TARGET;
  const selectTarget = (id: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("execution_target", id);
    for (const key of ["my_page", "running_page", "pending_page"]) url.searchParams.delete(key);
    window.history.pushState(null, "", url.pathname + url.search + url.hash);
    setCluster(null);
  };

  const [loadedClusterTarget, setLoadedClusterTarget] = useState("");
  const [cluster, setCluster] = useState<ClusterStats | null>(null);
  const [activeJobs, setActiveJobs] = useState<Job[]>([]);
  const [totalMyJobs, setTotalMyJobs] = useState(0);
  const [loading, setLoading] = useState(true);
  const [clusterRefreshFailed, setClusterRefreshFailed] = useState(false);
  const [loadedPages, setLoadedPages] = useState({ my: "", running: "", pending: "" });
  const [settledRequestKey, setSettledRequestKey] = useState("");
  const fetchRequest = useRef<{ key: string; active: boolean } | null>(null);

  // Each panel keeps its page in the URL under its own prefix so the three
  // paginations survive a route round-trip independently without colliding.
  const { page: myPage, pageSize: mySize, setPage: setMyPage, setPageSize: setMySize } =
    useUrlPagination({ prefix: "my", defaultPageSize: 5 });
  const { page: runningPage, pageSize: runningSize, setPage: setRunningPage, setPageSize: setRunningSize } =
    useUrlPagination({ prefix: "running", defaultPageSize: 5 });
  const { page: pendingPage, pageSize: pendingSize, setPage: setPendingPage, setPageSize: setPendingSize } =
    useUrlPagination({ prefix: "pending", defaultPageSize: 5 });

  const myKey = `${executionTarget}/${myPage}/${mySize}`;
  const runningKey = `${executionTarget}/${runningPage}/${runningSize}`;
  const pendingKey = `${executionTarget}/${pendingPage}/${pendingSize}`;
  const requestKey = `${myKey}/${runningKey}/${pendingKey}`;

  const fetchData = useCallback(async (isBackground = false) => {
    const key = requestKey;
    if (fetchRequest.current?.key === key && fetchRequest.current.active) return;
    const request = { key, active: true };
    fetchRequest.current = request;
    if (!isBackground) setLoading(true);
    try {
      const myParams = new URLSearchParams({
        ...(EXECUTION_TARGETS.length > 1 ? { execution_target: executionTarget } : {}),
        skip: ((myPage - 1) * mySize).toString(),
        limit: mySize.toString(),
      });
      const clusterParams = new URLSearchParams({
        ...(EXECUTION_TARGETS.length > 1 ? { execution_target: executionTarget } : {}),
        running_skip: ((runningPage - 1) * runningSize).toString(),
        running_limit: runningSize.toString(),
        pending_skip: ((pendingPage - 1) * pendingSize).toString(),
        pending_limit: pendingSize.toString(),
      });

      const [myJobsResult, clusterResult] = await Promise.allSettled([
        client(`/api/cluster/my-active-jobs?${myParams.toString()}`),
        client(`/api/cluster/stats?${clusterParams.toString()}`),
      ]);
      if (request !== fetchRequest.current) return;
      const myJobsData = myJobsResult.status === "fulfilled" ? myJobsResult.value : null;
      const clusterData = clusterResult.status === "fulfilled" ? clusterResult.value : null;
      setClusterRefreshFailed(!clusterData);
      if (myJobsResult.status === "rejected") console.warn("Failed to fetch active jobs", myJobsResult.reason);
      if (clusterResult.status === "rejected") console.warn("Failed to fetch cluster stats", clusterResult.reason);

      // Slurm IDs can collide across clusters; only a Magnus ID identifies
      // the same Job in both panels. Stale snapshots cannot advance its state.
      const runningMagnusIds = new Set<string>(
        ((clusterData?.resources?.is_stale ? [] : clusterData?.running_jobs ?? []) as Job[])
          .filter(job => job.job_type !== "N/A")
          .map(job => job.id),
      );

      const reconcileMyActive = (jobs: Job[]): Job[] => {
        const reconciled = jobs.map(job =>
          runningMagnusIds.has(job.id)
          && job.status !== "Running"
          && !job.is_releasing
            ? { ...job, status: "Running" }
            : job
        );
        // Keep the server's target grouping; stable sorting retains priority
        // and FIFO order within each lifecycle bucket.
        const bucketOrder: Record<string, number> = {
          "Running": 0,
          "Pending": 1,
          "Paused": 1,
          "Preparing": 2,
          "Building": 2,
        };
        const targetOrder = (job: Job) =>
          (job.execution_target ?? DEFAULT_EXECUTION_TARGET) === executionTarget ? 0 : 1;
        return reconciled.sort(
          (a, b) => targetOrder(a) - targetOrder(b)
            || (bucketOrder[a.status] ?? 99) - (bucketOrder[b.status] ?? 99)
        );
      };

      if (myJobsData && myJobsData.items) {
        setActiveJobs(reconcileMyActive(myJobsData.items));
        setTotalMyJobs(myJobsData.total);
      } else if (Array.isArray(myJobsData)) {
        setActiveJobs(reconcileMyActive(myJobsData));
        setTotalMyJobs(myJobsData.length);
      }

      setLoadedPages(previous => ({
        my: myJobsData && (myJobsData.items || Array.isArray(myJobsData)) ? myKey : previous.my,
        running: clusterData ? runningKey : previous.running,
        pending: clusterData ? pendingKey : previous.pending,
      }));
      if (clusterData) { setCluster(clusterData); setLoadedClusterTarget(executionTarget); }
    } catch (e) {
      if (request !== fetchRequest.current) return;
      console.error("Failed to fetch cluster data", e);
      setClusterRefreshFailed(true);
    } finally {
      request.active = false;
      if (request === fetchRequest.current) {
        setSettledRequestKey(key);
        setLoading(false);
      }
    }
  }, [executionTarget, myPage, mySize, runningPage, runningSize, pendingPage, pendingSize, myKey, runningKey, pendingKey, requestKey]);

  const {
    drawerProps,
    terminateDialogProps,
    errorDialogProps,
    handleNewJob,
    handleCloneJob,
    onClickTerminate
  } = useJobOperations({ onSuccess: fetchData });

  useEffect(() => { fetchData(); }, [fetchData]);
  usePolling(() => fetchData(true), POLL_INTERVAL);

  const resourceInfo = cluster && loadedClusterTarget === executionTarget ? presentClusterResources(cluster.resources, t, clusterRefreshFailed) : null;
  const waitingForPage = loading || settledRequestKey !== requestKey;
  const myPageReady = loadedPages.my === myKey;
  const runningPageReady = loadedPages.running === runningKey;
  const pendingPageReady = loadedPages.pending === pendingKey;

  return (
    <div className="pb-20 relative min-h-[calc(100vh-8rem)]">
      <style jsx global>{`
        ::-webkit-scrollbar { display: none; }
        html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      <ExecutionTargetSelect value={executionTarget} onChange={selectTarget} className="mb-6 max-w-xs" />
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">{t("nav.cluster")}</h1>
          <p className="text-zinc-500 text-sm mt-1">{t("cluster.welcome", { name: currentUser?.name || "" })}</p>
        </div>
        <button
          onClick={handleNewJob}
          className="shrink-0 whitespace-nowrap bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50"
        >
          <Plus className="w-4 h-4" /> {t("cluster.newJob")}
        </button>
      </div>

      {/* Cluster Resource Cards */}
      <div className={cn("grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8 transition-opacity", resourceInfo?.stale && "opacity-60")}>
        <div className="bg-zinc-900/40 border border-zinc-800 p-5 rounded-xl backdrop-blur-sm flex flex-col justify-between h-full">
          <div className="flex items-center gap-2 text-cyan-400 mb-2">
            <Cpu className="w-4 h-4" />
            <span className="text-sm font-bold uppercase tracking-wider">{t("cluster.availableCpuMem")}</span>
          </div>
          {cluster ? (
            <>
              <div className="flex items-baseline gap-1.5 mb-2">
                <span className="text-3xl font-bold text-white">{resourceInfo?.cpuFree}</span>
                <span className="text-zinc-500 text-sm">/ {cluster.resources.cpu_total} {t("cluster.cores")}</span>
              </div>
              <div className="flex items-center gap-1.5 text-zinc-400 text-xs">
                <span className="font-mono">RAM:</span>
                <span>{resourceInfo?.memoryFree} / {resourceInfo?.memoryTotal}</span>
              </div>
            </>
          ) : clusterRefreshFailed ? (
            <div className="text-3xl font-bold text-zinc-500">{t("jobResources.unknown")}</div>
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
                <span className="text-3xl font-bold text-white">{resourceInfo?.gpuFree}</span>
                <span className="text-zinc-500 text-sm">/ {cluster.resources.total}</span>
              </div>
              <div className="flex items-center gap-1.5 text-zinc-400 text-xs font-mono">
                <Server className="w-3 h-3" />
                {cluster.resources.gpu_model}
              </div>
            </>
          ) : clusterRefreshFailed ? (
            <div className="text-3xl font-bold text-zinc-500">{t("jobResources.unknown")}</div>
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
          ) : clusterRefreshFailed ? (
            <div className="text-3xl font-bold text-zinc-500">{t("jobResources.unknown")}</div>
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
          ) : clusterRefreshFailed ? (
            <div className="text-3xl font-bold text-zinc-500">{t("jobResources.unknown")}</div>
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
              jobs={myPageReady ? activeJobs : []}
              loading={!myPageReady && waitingForPage}
              onClone={handleCloneJob}
              onTerminate={onClickTerminate}
              emptyMessage={myPageReady ? t("cluster.noActiveJobs") : t("cluster.loadFailed")}
              className="border-none min-h-[200px]"
            />
            {totalMyJobs > 0 && (
              <div className="px-4 pb-2 bg-zinc-900/30">
                <PaginationControls
                  currentPage={myPage}
                  totalPages={Math.ceil(totalMyJobs / mySize)}
                  pageSize={mySize}
                  totalItems={totalMyJobs}
                  onPageChange={setMyPage}
                  onPageSizeChange={setMySize}
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
              jobs={runningPageReady ? cluster?.running_jobs || [] : []}
              loading={!runningPageReady && waitingForPage}
              onClone={handleCloneJob}
              onTerminate={onClickTerminate}
              emptyMessage={runningPageReady ? t("cluster.noRunningJobs") : t("cluster.loadFailed")}
              className="border-none min-h-[175px]"
            />
            {(cluster?.total_running || 0) > 0 && (
              <div className="px-4 pb-2 bg-zinc-900/30">
                <PaginationControls
                  currentPage={runningPage}
                  totalPages={Math.ceil((cluster?.total_running || 0) / runningSize)}
                  pageSize={runningSize}
                  totalItems={cluster?.total_running || 0}
                  onPageChange={setRunningPage}
                  onPageSizeChange={setRunningSize}
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
              jobs={pendingPageReady ? cluster?.pending_jobs || [] : []}
              loading={!pendingPageReady && waitingForPage}
              onClone={handleCloneJob}
              onTerminate={onClickTerminate}
              emptyMessage={pendingPageReady ? t("cluster.queueEmpty") : t("cluster.loadFailed")}
              className="border-none min-h-[175px]"
            />
            {(cluster?.total_pending || 0) > 0 && (
              <div className="px-4 pb-2 bg-zinc-900/30">
                <PaginationControls
                  currentPage={pendingPage}
                  totalPages={Math.ceil((cluster?.total_pending || 0) / pendingSize)}
                  pageSize={pendingSize}
                  totalItems={cluster?.total_pending || 0}
                  onPageChange={setPendingPage}
                  onPageSizeChange={setPendingSize}
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

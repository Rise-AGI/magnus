// front_end/src/app/(main)/jobs/[id]/page.tsx
"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft, Terminal, Clock, GitBranch, Cpu, Box, AlignLeft, RefreshCw, Activity,
  ArrowDownToLine, ArrowUpToLine, ChevronUp, ChevronDown, Copy, Check, SquareX
} from "lucide-react";
import { AnsiUp } from "ansi_up";
import { client } from "@/lib/api";
import { CopyableText } from "@/components/ui/copyable-text";
import { POLL_INTERVAL } from "@/lib/config";
import { Job } from "@/types/job";
import { formatBeijingTime } from "@/lib/utils";
import { JobPriorityBadge } from "@/components/jobs/job-priority-badge";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import RenderMarkdown from "@/components/ui/render-markdown";
import { JobDrawer } from "@/components/jobs/job-drawer";
import { useJobOperations } from "@/hooks/use-job-operations";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { NotFound } from "@/components/ui/not-found";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { useBackNavigation } from "@/hooks/use-back-navigation";

const ansiUp = new AnsiUp();

function processCarriageReturns(text: string): string {
  return text.replaceAll("\r", "\n");
}

export default function JobDetailsPage() {
  const { user } = useAuth();
  const { t } = useLanguage();
  const params = useParams();
  const router = useRouter();
  const jobId = params.id as string;
  const isSlurmTask = decodeURIComponent(jobId).endsWith("(slurm)");
  const { backLabel, goBack } = useBackNavigation("/jobs", t("jobDetail.backToJobs"));

  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"console" | "description" | "metrics">("console");

  const [logs, setLogs] = useState("");
  const [logPage, setLogPage] = useState(-1);
  const [logTotalPages, setLogTotalPages] = useState(1);
  const [followMode, setFollowMode] = useState(false);
  const [pendingScroll, setPendingScroll] = useState<"top" | "bottom" | null>(null);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const lastClickTimeRef = useRef(0);

  const [copiedCommand, setCopiedCommand] = useState(false);
  const copyToClipboard = async (text: string, setCopied: (v: boolean) => void) => {
    try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
    } catch (err) {
        console.error('Failed to copy', err);
    }
  };

  const fetchJob = useCallback(async (isBackground = false) => {
    if (isSlurmTask) return;

    if (!isBackground) setLoading(true);
    try {
      const data = await client(`/api/jobs/${jobId}`);
      setJob(data);
    } catch (e) {
      console.error("Failed to fetch job", e);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [jobId, isSlurmTask]);

  const { drawerProps, handleCloneJob, onClickTerminate, terminateDialogProps } = useJobOperations({
    onSuccess: () => {
      sessionStorage.setItem('magnus_new_job', '1');
      router.push("/jobs");
    },
    onTerminateSuccess: () => fetchJob(false)
  });

  useEffect(() => {
    if (isSlurmTask) {
      setLoading(false);
      return;
    }
    fetchJob();
    const interval = setInterval(() => fetchJob(true), POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [jobId, isSlurmTask, fetchJob]);

  // Fetch logs for a specific page
  const fetchLogs = useCallback(async (page: number) => {
    try {
      const res = await client(`/api/jobs/${jobId}/logs?page=${page}`);
      setLogs(res.logs || "");
      setLogPage(res.page ?? 0);
      setLogTotalPages(res.total_pages ?? 1);
    } catch (e) {
      console.error("Failed to fetch logs", e);
    }
  }, [jobId]);

  // Initial log load + auto-enable follow mode for running jobs
  useEffect(() => {
    if (isSlurmTask) return;
    fetchLogs(-1);
    if (job && ['Pending', 'Preparing', 'Queued', 'Running'].includes(job.status)) {
      setFollowMode(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, isSlurmTask, job?.status, fetchLogs]);

  // Follow mode polling (5x slower than POLL_INTERVAL)
  useEffect(() => {
    if (!followMode) return;
    if (job && !['Pending', 'Preparing', 'Queued', 'Running'].includes(job.status)) {
      setFollowMode(false);
      return;
    }
    const interval = setInterval(() => fetchLogs(-1), POLL_INTERVAL * 5);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [followMode, job?.status, fetchLogs]);

  // Execute pending scroll after logs update
  useEffect(() => {
    if (pendingScroll && logContainerRef.current) {
      requestAnimationFrame(() => {
        if (!logContainerRef.current) return;
        const top = pendingScroll === "top" ? 0 : logContainerRef.current.scrollHeight;
        logContainerRef.current.scrollTo({ top, behavior: "auto" });
        setPendingScroll(null);
      });
    }
  }, [logs, pendingScroll]);

  // Follow mode: auto-scroll to bottom on new logs
  useEffect(() => {
    if (followMode && logContainerRef.current) {
      logContainerRef.current.scrollTo({ top: logContainerRef.current.scrollHeight, behavior: "auto" });
    }
  }, [logs, followMode]);

  // Exit follow mode on scroll up
  const handleLogScroll = () => {
    if (!followMode || !logContainerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = logContainerRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
    if (!isAtBottom) {
      setFollowMode(false);
    }
  };

  const effectivePage = logPage < 0 ? Math.max(0, logTotalPages - 1) : logPage;

  const goToFirstPage = () => {
    setFollowMode(false);
    setLogPage(0);
    fetchLogs(0);
    setPendingScroll("top");
  };

  const goToPrevPage = () => {
    if (effectivePage <= 0) return;
    setFollowMode(false);
    const newPage = effectivePage - 1;
    setLogPage(newPage);
    fetchLogs(newPage);
    setPendingScroll("bottom");
  };

  const goToNextPage = () => {
    if (effectivePage >= logTotalPages - 1) return;
    setFollowMode(false);
    const newPage = effectivePage + 1;
    setLogPage(newPage);
    fetchLogs(newPage);
    setPendingScroll("top");
  };

  const goToLastPage = () => {
    const now = Date.now();
    const isDoubleClick = now - lastClickTimeRef.current < 300;
    lastClickTimeRef.current = now;

    if (isDoubleClick) {
      setFollowMode(true);
    } else {
      setFollowMode(false);
    }

    setLogPage(-1);
    fetchLogs(-1);
    setPendingScroll("bottom");
  };

  if (isSlurmTask) {
    return (
      <div className="flex flex-col items-center justify-center h-[60vh] text-zinc-400 gap-6">
        <div className="bg-zinc-900/50 p-6 rounded-2xl border border-zinc-800 text-center max-w-md">
          <div className="w-12 h-12 bg-zinc-800 rounded-full flex items-center justify-center mx-auto mb-4">
            <Terminal className="w-6 h-6 text-zinc-500" />
          </div>
          <h2 className="text-xl font-bold text-zinc-200 mb-2">{t("jobDetail.externalTask")}</h2>
          <p className="text-zinc-500 text-sm mb-6 leading-relaxed">
            {t("jobDetail.externalTaskDesc")}
          </p>
          <button
            onClick={() => router.back()}
            className="px-6 py-2 bg-blue-600 text-white hover:bg-blue-500 text-sm font-medium rounded-lg transition-colors"
          >
            {t("jobDetail.goBack")}
          </button>
        </div>
      </div>
    );
  }

  if (loading) {
    return <div className="flex items-center justify-center h-[50vh] text-zinc-500">{t("jobDetail.loading")}</div>;
  }

  if (!job) {
    return (
      <NotFound
        title={t("jobDetail.notFound")}
        description={t("jobDetail.notFoundDesc", { id: decodeURIComponent(jobId) })}
        buttonText={t("jobDetail.returnToJobs")}
        onBack={() => router.push("/jobs")}
      />
    );
  }

  return (
    <div className="max-w-6xl mx-auto pb-20 px-4 lg:px-0">
      <style jsx global>{`
        ::-webkit-scrollbar { display: none; }
        html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      {/* Top Navigation */}
      <div className="mb-8">
        <button
          onClick={() => goBack()}
          className="flex items-center gap-2 text-zinc-400 hover:text-white transition-colors text-sm mb-6 group"
        >
          <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" />
          {backLabel}
        </button>

        {/* Header Section */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6">
          <div className="flex-1 min-w-0 pr-8">

            {/* Task Name & Priority */}
            <div className="flex items-center gap-4 mb-3 group">
              <CopyableText
                text={job.task_name}
                variant="text"
                className="!w-auto text-3xl font-bold text-white tracking-tight leading-tight"
              />
              <div className="flex-shrink-0">
                <JobPriorityBadge type={job.job_type} />
              </div>
            </div>

            {/* ID & Time */}
            <div className="flex items-center gap-1 text-sm text-zinc-500 font-mono">
              <div className="flex items-center gap-2">
                <span className="text-zinc-600">ID:</span>
                <CopyableText text={job.id} variant="id" />
              </div>
              <span className="text-zinc-700">|</span>
              <span className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                {formatBeijingTime(job.created_at)}
              </span>
            </div>

          </div>

          {/* Status Card */}
          <div className="flex items-center gap-4 bg-zinc-900/50 border border-zinc-800 px-6 py-4 rounded-xl backdrop-blur-sm flex-shrink-0 shadow-lg shadow-black/20">
            <JobStatusBadge status={job.status} size="md" />
            <div className="flex flex-col">
              <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">{t("jobDetail.status")}</span>
              <span className={`text-base font-bold tracking-wide
                ${job.status === "Running" ? "text-blue-400" :
                  job.status === "Success" ? "text-green-400" :
                  job.status === "Failed" ? "text-red-400" :
                  job.status === "Preparing" ? "text-cyan-400" : "text-zinc-300"}`}>
                {/* status 是运行时动态值，无法满足 TranslationKey 字面量类型 */}
                {t(`jobStatus.${(job.status === "Queued" ? "pending" : job.status.toLowerCase())}` as any)?.toUpperCase() || job.status.toUpperCase()}
              </span>
            </div>
            {/* Owner */}
            {job.user && (
              <div className="ml-4 pl-4 border-l border-zinc-700/50 flex items-center gap-3">
                {job.user.avatar_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={job.user.avatar_url}
                    alt={job.user.name}
                    className="w-8 h-8 rounded-full border border-zinc-700/50 object-cover shadow-sm"
                  />
                ) : (
                  <div className="w-8 h-8 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-xs font-bold border border-indigo-500/30">
                    {job.user.name.substring(0, 2).toUpperCase()}
                  </div>
                )}
                <div className="flex flex-col">
                  <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">{t("jobDetail.creator")}</span>
                  <span className="text-sm font-medium text-zinc-200">{job.user.name}</span>
                </div>
              </div>
            )}

            <div className="ml-4 pl-4 border-l border-zinc-700/50 h-full flex items-center">
              {/* Clone Button */}
              <button
                onClick={() => handleCloneJob(job)}
                className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                title={t("jobDetail.cloneJob")}
              >
                <RefreshCw className="w-5 h-5" />
              </button>
              {/* Terminate Button */}
              {user?.id === job.user?.id && ["Pending", "Preparing", "Queued", "Running", "Paused"].includes(job.status) && (
                <button
                  onClick={() => onClickTerminate(job)}
                  className="ml-2 p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                  title={t("jobDetail.terminateTask")}
                >
                  <SquareX className="w-5 h-5" />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* Left Column: Configuration */}
        <div className="lg:col-span-1 flex flex-col gap-3 h-full">

          {/* Repository Info */}
          <div className="shrink-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <div className="px-4 py-2.5 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-zinc-400" />
              <h3 className="text-sm font-semibold text-zinc-200">{t("jobDetail.repository")}</h3>
            </div>
            <div className="p-4 space-y-3">

              {/* Repo Name */}
              <div>
                <div className="flex items-center gap-2 mb-1.5">
                  <a
                    href={`https://github.com/${job.namespace}/${job.repo_name}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs font-medium uppercase tracking-wider text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1 cursor-pointer transition-colors w-fit"
                    title={t("jobDetail.openRepoGithub")}
                  >
                    {t("jobDetail.githubRepo")}
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                  </a>
                </div>

                <div className="flex items-center gap-2 text-sm text-zinc-200 bg-zinc-950 px-3 py-1.5 rounded-lg border border-zinc-800/50 shadow-inner">
                  <Box className="w-4 h-4 text-zinc-500 flex-shrink-0" />
                  <CopyableText
                    text={`${job.namespace}/${job.repo_name}`}
                    variant="text"
                    className="text-zinc-200 font-mono"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-2.5">
                {/* Branch */}
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    {job.branch ? (
                      <a
                        href={`https://github.com/${job.namespace}/${job.repo_name}/tree/${job.branch}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs font-medium uppercase tracking-wider text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1 cursor-pointer w-fit"
                        title={t("jobDetail.viewBranchTree")}
                      >
                        {t("jobDetail.branch")}
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                      </a>
                    ) : (
                      <span className="text-xs font-medium uppercase tracking-wider text-zinc-500">{t("jobDetail.branch")}</span>
                    )}
                  </div>
                  <div className="text-sm font-mono text-zinc-300 bg-zinc-950/50 px-2 py-1.5 rounded border border-zinc-800/50">
                    <CopyableText
                      text={job.branch ?? "TBD"}
                      variant="id"
                      className="text-zinc-300"
                    />
                  </div>
                </div>

                {/* Commit SHA */}
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    {job.commit_sha ? (
                      <a
                        href={`https://github.com/${job.namespace}/${job.repo_name}/commit/${job.commit_sha}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs font-medium uppercase tracking-wider text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1 cursor-pointer w-fit"
                        title={t("jobDetail.viewCommitDetails")}
                      >
                        {t("jobDetail.commitSha")}
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                      </a>
                    ) : (
                      <span className="text-xs font-medium uppercase tracking-wider text-zinc-500">{t("jobDetail.commitSha")}</span>
                    )}
                  </div>
                  <div className="text-sm font-mono text-zinc-400 bg-zinc-950/50 px-2 py-1.5 rounded border border-zinc-800/50">
                    <CopyableText
                      text={job.commit_sha ?? "TBD"}
                      copyValue={job.commit_sha ?? ""}
                      variant="id"
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Resources */}
          <div className="shrink-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <div className="px-4 py-2.5 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-2">
              <Cpu className="w-4 h-4 text-zinc-400" />
              <h3 className="text-sm font-semibold text-zinc-200">{t("jobDetail.resources")}</h3>
            </div>
            <div className="p-4 grid grid-cols-2 gap-x-4 gap-y-3">
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1">{t("jobDetail.cpuCores")}</label>
                <span className="text-sm text-white font-medium block">
                  {job.cpu_count ? job.cpu_count : <span className="text-zinc-500 text-xs">{t("jobDetail.stationDefault")}</span>}
                </span>
              </div>
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1">{t("jobDetail.memory")}</label>
                <span className="text-sm text-white font-medium block">
                  {job.memory_demand ? job.memory_demand : <span className="text-zinc-500 text-xs">{t("jobDetail.stationDefault")}</span>}
                </span>
              </div>
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1">{t("jobDetail.accelerator")}</label>
                <span className="text-sm text-white font-medium block">
                  {job.gpu_type === "CPU" ? t("jobDetail.cpuOnly") : job.gpu_type}
                </span>
              </div>
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1">{t("jobDetail.gpuCount")}</label>
                <span className="text-sm text-white font-medium block">{job.gpu_count} {job.gpu_count === 1 ? "GPU" : "GPUs"}</span>
              </div>
            </div>
          </div>

          {/* Entry Command */}
          <div className="flex-1 min-h-0 flex flex-col bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden shadow-sm">
            <div className="shrink-0 px-4 py-2.5 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Terminal className="w-4 h-4 text-zinc-400" />
                <h3 className="text-sm font-semibold text-zinc-200">{t("jobDetail.entryCommand")}</h3>
              </div>
              <button
                onClick={() => copyToClipboard(job.entry_command, setCopiedCommand)}
                className="text-zinc-500 hover:text-zinc-200 transition-colors"
                title={t("jobDetail.copyFullCommand")}
              >
                {copiedCommand ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
            
            <div className="flex-1 relative min-h-0 bg-zinc-950 group">
              <div className="absolute inset-0 overflow-y-auto p-4 custom-scrollbar">
                <pre className="text-xs font-mono text-green-400 leading-relaxed whitespace-pre-wrap break-all selection:bg-green-900/50 selection:text-green-200">
                  {job.entry_command}
                </pre>
              </div>
            </div>
          </div>

        </div>

        {/* Right Column: Console / Description / Metrics */}
        <div className="lg:col-span-2 flex flex-col min-h-[400px] lg:h-[700px] bg-[#0c0c0e] border border-zinc-800 rounded-xl overflow-hidden shadow-2xl">

          {/* Tab Navigation */}
          <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between select-none">
            <div className="flex items-center gap-6">

              <div
                onClick={() => setActiveTab("console")}
                className={`flex items-center gap-2 text-sm font-semibold transition-colors cursor-pointer
                  ${activeTab === "console" ? "text-zinc-200" : "text-zinc-500 hover:text-zinc-300"}`}
              >
                <Terminal className={`w-4 h-4 ${activeTab === "console" ? "text-zinc-400" : "text-zinc-600"}`} />
                <span>{t("jobDetail.consoleOutput")}</span>
                {job.status === "Running" && (
                  <span className="flex h-1.5 w-1.5 relative ml-0.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-green-500"></span>
                  </span>
                )}
              </div>

              <div
                onClick={() => setActiveTab("description")}
                className={`flex items-center gap-2 text-sm font-semibold transition-colors cursor-pointer
                  ${activeTab === "description" ? "text-zinc-200" : "text-zinc-500 hover:text-zinc-300"}`}
              >
                <AlignLeft className={`w-4 h-4 ${activeTab === "description" ? "text-zinc-400" : "text-zinc-600"}`} />
                <span>{t("jobDetail.description")}</span>
              </div>

              <div
                onClick={() => setActiveTab("metrics")}
                className={`flex items-center gap-2 text-sm font-semibold transition-colors cursor-pointer
                  ${activeTab === "metrics" ? "text-zinc-200" : "text-zinc-500 hover:text-zinc-300"}`}
              >
                <Activity className={`w-4 h-4 ${activeTab === "metrics" ? "text-zinc-400" : "text-zinc-600"}`} />
                <span>{t("jobDetail.metrics")}</span>
              </div>

            </div>

            {job.status === "Running" && (
              <div className="text-xs text-zinc-500 font-medium flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500/50"></span>
                {t("jobDetail.live")}
              </div>
            )}
          </div>

          {/* Content Area */}
          <div className="relative flex-1 min-h-0 bg-zinc-950">

            {activeTab === 'console' && (
              <>
                {logs && (
                  <div className="absolute bottom-6 right-6 flex flex-col gap-1.5 z-10">
                    <button
                      onClick={goToFirstPage}
                      className="p-2 bg-zinc-800/80 backdrop-blur-sm border border-zinc-700/50 text-zinc-400 hover:text-white hover:bg-zinc-700 hover:border-zinc-600 rounded-lg shadow-lg transition-all active:scale-95"
                      title={t("jobDetail.firstPage")}
                    >
                      <ArrowUpToLine className="w-4 h-4" />
                    </button>
                    <button
                      onClick={goToPrevPage}
                      disabled={effectivePage <= 0}
                      className="p-2 bg-zinc-800/80 backdrop-blur-sm border border-zinc-700/50 text-zinc-400 hover:text-white hover:bg-zinc-700 hover:border-zinc-600 rounded-lg shadow-lg transition-all active:scale-95 disabled:opacity-30 disabled:pointer-events-none"
                      title={t("jobDetail.prevPage")}
                    >
                      <ChevronUp className="w-4 h-4" />
                    </button>
                    <button
                      onClick={goToNextPage}
                      disabled={effectivePage >= logTotalPages - 1}
                      className="p-2 bg-zinc-800/80 backdrop-blur-sm border border-zinc-700/50 text-zinc-400 hover:text-white hover:bg-zinc-700 hover:border-zinc-600 rounded-lg shadow-lg transition-all active:scale-95 disabled:opacity-30 disabled:pointer-events-none"
                      title={t("jobDetail.nextPage")}
                    >
                      <ChevronDown className="w-4 h-4" />
                    </button>
                    <button
                      onClick={goToLastPage}
                      className={`p-2 backdrop-blur-sm border rounded-lg shadow-lg transition-all active:scale-95
                        ${followMode
                          ? "bg-green-900/20 border-green-800/50 text-green-400"
                          : "bg-zinc-800/80 border-zinc-700/50 text-zinc-400 hover:text-white hover:bg-zinc-700 hover:border-zinc-600"}`}
                      title={followMode ? t("jobDetail.followMode") : t("jobDetail.lastPageFollow")}
                    >
                      <ArrowDownToLine className="w-4 h-4" />
                    </button>
                  </div>
                )}

                <div
                  ref={logContainerRef}
                  onScroll={handleLogScroll}
                  className="absolute inset-0 overflow-auto p-5 custom-scrollbar font-mono text-xs leading-5"
                >
                  {logs ? (
                    <pre
                      className="text-zinc-300 whitespace-pre-wrap break-all pb-10"
                      dangerouslySetInnerHTML={{ __html: ansiUp.ansi_to_html(processCarriageReturns(logs)) }}
                    />
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-zinc-600 gap-3 min-h-[400px]">
                      <Terminal className="w-10 h-10 opacity-20" />
                      <p>
                        {job && ['Pending', 'Preparing', 'Queued', 'Running'].includes(job.status)
                          ? t("jobDetail.waitingOutput")
                          : t("jobDetail.noOutput")}
                      </p>
                    </div>
                  )}
                </div>
              </>
            )}

            {activeTab === "description" && (
              <div className="absolute inset-0 overflow-auto p-5 custom-scrollbar">
                <div className="min-h-[200px]">
                  {job.description ? (
                    <RenderMarkdown content={job.description} />
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-zinc-600 gap-3 min-h-[200px] italic">
                      <AlignLeft className="w-8 h-8 opacity-20" />
                      {t("jobDetail.noDescriptionProvided")}
                    </div>
                  )}
                </div>
              </div>
            )}

            {activeTab === "metrics" && (
              <div className="absolute inset-0 overflow-auto p-5 custom-scrollbar">
                <div className="h-full flex flex-col items-center justify-center text-zinc-500 gap-4 min-h-[400px]">
                  <div className="relative">
                    <Activity className="w-12 h-12 opacity-20" />
                    <div className="absolute -bottom-1 -right-1 bg-amber-500/20 text-amber-500 p-1 rounded-full">
                      <RefreshCw className="w-4 h-4 animate-spin-slow" />
                    </div>
                  </div>
                  <div className="text-center">
                    <p className="text-zinc-200 font-bold text-lg mb-1">{t("jobDetail.comingSoon")}</p>
                    <p className="text-zinc-500 text-sm">{t("jobDetail.underConstruction")}</p>
                  </div>
                </div>
              </div>
            )}
          </div>

        </div>

      </div>

      <JobDrawer {...drawerProps} />
      <ConfirmationDialog {...terminateDialogProps} />
    </div>
  );
}
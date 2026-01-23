// front_end/src/app/(main)/services/[id]/page.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft, Server, Clock, GitBranch, Cpu, Box, Terminal, RefreshCw,
  Power, Trash2, Loader2, FileQuestion, ExternalLink, Copy, Check, AlignLeft
} from "lucide-react";

import { client } from "@/lib/api";
import { formatBeijingTime } from "@/lib/utils";
import { useAuth } from "@/context/auth-context";
import { POLL_INTERVAL } from "@/lib/config";

import { CopyableText } from "@/components/ui/copyable-text";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { ServiceDrawer } from "@/components/services/service-drawer";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import RenderMarkdown from "@/components/ui/render-markdown";

import { Service } from "@/types/service";

export default function ServiceDetailsPage() {
  const params = useParams();
  const router = useRouter();
  const { user: currentUser } = useAuth();
  const serviceId = params.id as string;

  // Data States
  const [service, setService] = useState<Service | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  // Action States
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [pendingAction, setPendingAction] = useState<"delete" | "toggle" | null>(null);

  // Copy States
  const [copiedCommand, setCopiedCommand] = useState(false);
  const [copiedDescription, setCopiedDescription] = useState(false);

  // Fetch Service Details
  const fetchService = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const data = await client(`/api/services/${serviceId}`);
      setService(data);
      setNotFound(false);
    } catch (e) {
      console.error("Failed to fetch service", e);
      if (!isBackground) setNotFound(true);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [serviceId]);

  useEffect(() => {
    fetchService();
    const interval = setInterval(() => fetchService(true), POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchService]);

  // Actions
  const handleToggleClick = () => {
    setPendingAction("toggle");
    setConfirmOpen(true);
  };

  const handleDeleteClick = () => {
    setPendingAction("delete");
    setConfirmOpen(true);
  };

  const handleConfirmAction = async () => {
    if (!pendingAction || !service) return;

    setActionLoading(true);
    try {
      if (pendingAction === "delete") {
        await client(`/api/services/${service.id}`, { method: "DELETE" });
        router.push("/services");
      } else if (pendingAction === "toggle") {
        const updatedService = {
          ...service,
          is_active: !service.is_active,
        };
        await client("/api/services", {
          method: "POST",
          json: updatedService,
        });
        setConfirmOpen(false);
        setPendingAction(null);
        fetchService(true);
      }
    } catch (e: any) {
      alert(`Operation failed: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDrawerSuccess = () => {
    setIsDrawerOpen(false);
    fetchService();
  };

  const copyToClipboard = async (text: string, setCopied: (v: boolean) => void) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (err) {
      console.error("Failed to copy", err);
    }
  };

  const getDialogConfig = () => {
    if (!pendingAction || !service) {
      return { title: "", description: "", variant: "default" as const, confirmText: "" };
    }

    if (pendingAction === "delete") {
      return {
        title: "Delete Service",
        description: (
          <span>
            Are you sure you want to delete service <strong className="text-white">{service.name}</strong>?
            This action cannot be undone and will terminate any running instances.
          </span>
        ),
        variant: "danger" as const,
        confirmText: "Delete Service",
      };
    } else {
      const isStopping = service.is_active;
      return {
        title: isStopping ? "Stop Service" : "Start Service",
        description: isStopping ? (
          <span>
            Are you sure you want to stop <strong className="text-white">{service.name}</strong>?
            The proxy endpoint will stop accepting traffic.
          </span>
        ) : (
          <span>
            Are you sure you want to activate <strong className="text-white">{service.name}</strong>?
            This will enable traffic routing and scale up resources on demand.
          </span>
        ),
        variant: isStopping ? "danger" as const : "default" as const,
        confirmText: isStopping ? "Stop Service" : "Start Service",
      };
    }
  };

  const dialogConfig = getDialogConfig();

  // Loading State
  if (loading) {
    return (
      <div className="flex h-[50vh] items-center justify-center text-zinc-500">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    );
  }

  // Not Found State
  if (notFound || !service) {
    return (
      <div className="flex flex-col items-center justify-center h-[60vh] text-zinc-400 gap-6">
        <div className="bg-zinc-900/50 p-8 rounded-2xl border border-zinc-800 text-center max-w-md shadow-2xl backdrop-blur-sm">
          <div className="w-16 h-16 bg-zinc-800/80 rounded-full flex items-center justify-center mx-auto mb-6 border border-zinc-700/50 shadow-inner">
            <FileQuestion className="w-8 h-8 text-zinc-500" />
          </div>
          <h2 className="text-xl font-bold text-zinc-200 mb-2 tracking-tight">Service Not Found</h2>
          <p className="text-zinc-500 text-sm mb-8 leading-relaxed">
            The service <code className="bg-zinc-800 px-1.5 py-0.5 rounded text-zinc-400 font-mono text-xs">{decodeURIComponent(serviceId)}</code> could not be located in the registry.
            <br />It may have been deleted or the ID is incorrect.
          </p>
          <button
            onClick={() => router.push("/services")}
            className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-all shadow-lg shadow-blue-900/20 active:scale-95 flex items-center justify-center gap-2 mx-auto"
          >
            <ArrowLeft className="w-4 h-4" /> Return to Services
          </button>
        </div>
      </div>
    );
  }

  const isOwner = currentUser?.id === service.owner_id;
  const displayUser = service.owner || {
    id: service.owner_id,
    name: "Unknown",
    email: undefined,
    avatar_url: undefined,
    feishu_open_id: "",
  };

  // Service Status Node
  let statusNode;
  const currentJobStatus = service.current_job?.status;
  const hasLiveJob = service.current_job && ["Pending", "Running", "Paused"].includes(currentJobStatus || "");

  if (!service.is_active) {
    statusNode = (
      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-zinc-800 text-zinc-500 border border-zinc-700">
        Inactive
      </span>
    );
  } else if (hasLiveJob) {
    statusNode = (
      <div
        onClick={() => router.push(`/jobs/${service.current_job!.id}?from=services&id=${service.id}`)}
        className="cursor-pointer hover:opacity-80 transition-opacity"
      >
        <JobStatusBadge status={currentJobStatus!} size="md" />
      </div>
    );
  } else {
    statusNode = (
      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-teal-500/10 text-teal-400 border border-teal-500/20">
        <span className="relative flex h-2 w-2 mr-1.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-teal-400 opacity-75"></span>
          <span className="relative inline-flex rounded-full h-2 w-2 bg-teal-500"></span>
        </span>
        Idle
      </span>
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
          onClick={() => router.push("/services")}
          className="flex items-center gap-2 text-zinc-400 hover:text-white transition-colors text-sm mb-6 group"
        >
          <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" />
          Back to Services
        </button>

        {/* Header Section */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6">
          <div className="flex-1 min-w-0 pr-8">
            {/* Service Name */}
            <div className="flex items-center gap-4 mb-3">
              <Server className="w-8 h-8 text-blue-500" />
              <CopyableText
                text={service.name}
                variant="text"
                className="!w-auto text-3xl font-bold text-white tracking-tight leading-tight"
              />
            </div>

            {/* ID & Time */}
            <div className="flex items-center gap-1 text-sm text-zinc-500 font-mono">
              <div className="flex items-center gap-2">
                <span className="text-zinc-600">ID:</span>
                <CopyableText text={service.id} variant="id" />
              </div>
              <span className="text-zinc-700">|</span>
              <span className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                {formatBeijingTime(service.updated_at)}
              </span>
            </div>
          </div>

          {/* Status Card */}
          <div className="flex items-center gap-4 bg-zinc-900/50 border border-zinc-800 px-6 py-4 rounded-xl backdrop-blur-sm flex-shrink-0 shadow-lg shadow-black/20">
            {statusNode}
            <div className="flex flex-col">
              <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">Status</span>
              <span className={`text-base font-bold tracking-wide ${
                !service.is_active ? "text-zinc-400" :
                hasLiveJob && currentJobStatus === "Running" ? "text-blue-400" :
                hasLiveJob && currentJobStatus === "Pending" ? "text-amber-400" :
                "text-teal-400"
              }`}>
                {!service.is_active ? "INACTIVE" : hasLiveJob ? currentJobStatus?.toUpperCase() : "IDLE"}
              </span>
            </div>

            {/* Owner */}
            <div className="ml-4 pl-4 border-l border-zinc-700/50 flex items-center gap-3">
              {displayUser.avatar_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={displayUser.avatar_url}
                  alt={displayUser.name}
                  className="w-8 h-8 rounded-full border border-zinc-700/50 object-cover shadow-sm"
                />
              ) : (
                <div className="w-8 h-8 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-xs font-bold border border-indigo-500/30">
                  {displayUser.name.substring(0, 2).toUpperCase()}
                </div>
              )}
              <div className="flex flex-col">
                <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">Manager</span>
                <span className="text-sm font-medium text-zinc-200">{displayUser.name}</span>
              </div>
            </div>

            <div className="ml-4 pl-4 border-l border-zinc-700/50 h-full flex items-center gap-2">
              {/* Clone Button */}
              <button
                onClick={() => setIsDrawerOpen(true)}
                className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                title={isOwner ? "Edit / Clone Service" : "Clone Service"}
              >
                <RefreshCw className="w-5 h-5" />
              </button>

              {/* Toggle Button */}
              {isOwner && (
                <button
                  onClick={handleToggleClick}
                  className={`p-2 rounded-lg transition-colors border shadow-sm ${service.is_active
                    ? "bg-teal-900/20 hover:bg-teal-900/40 text-teal-400 border-teal-500/20"
                    : "bg-zinc-800 hover:bg-zinc-700 text-zinc-500 hover:text-zinc-300 border-zinc-700/50"
                    }`}
                  title={service.is_active ? "Stop Service" : "Start Service"}
                >
                  <Power className="w-5 h-5" />
                </button>
              )}

              {/* Delete Button */}
              {isOwner && (
                <button
                  onClick={handleDeleteClick}
                  className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                  title="Delete Service"
                >
                  <Trash2 className="w-5 h-5" />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">

        {/* Left Column: Service 自身元素 */}
        <div className="lg:col-span-2 flex flex-col gap-6">

          {/* Description */}
          <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlignLeft className="w-4 h-4 text-zinc-400" />
                <h3 className="text-sm font-semibold text-zinc-200">Description</h3>
              </div>
              {service.description && (
                <button
                  onClick={() => copyToClipboard(service.description || "", setCopiedDescription)}
                  className="text-zinc-500 hover:text-zinc-200 transition-colors"
                  title="Copy Description"
                >
                  {copiedDescription ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
              )}
            </div>
            <div className="p-5">
              {service.description ? (
                <p className="text-zinc-300 text-sm leading-relaxed break-all">{service.description}</p>
              ) : (
                <p className="text-zinc-600 italic text-sm">No description provided.</p>
              )}
            </div>
          </div>

          {/* Service Configuration */}
          <div className="shrink-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-2">
              <Server className="w-4 h-4 text-zinc-400" />
              <h3 className="text-sm font-semibold text-zinc-200">Service Configuration</h3>
            </div>
            <div className="p-5 grid grid-cols-2 gap-5">
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1.5">Request Timeout</label>
                <span className="text-base text-white font-medium block">{service.request_timeout}s</span>
              </div>
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1.5">Idle Timeout</label>
                <span className="text-base text-white font-medium block">
                  {service.idle_timeout === 0 ? (
                    <span className="text-zinc-500">0s (Never Scale Down)</span>
                  ) : (
                    <>{service.idle_timeout}s</>
                  )}
                </span>
              </div>
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1.5">Max Concurrency</label>
                <span className="text-base text-white font-medium block">{service.max_concurrency}</span>
              </div>
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1.5">Job Type</label>
                <span className="text-base text-white font-medium block">{service.job_type}</span>
              </div>
            </div>
          </div>

        </div>

        {/* Right Column: Job 相关元素 */}
        <div className="lg:col-span-3 flex flex-col gap-6">

          {/* Repository Info */}
          <div className="shrink-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-zinc-400" />
              <h3 className="text-sm font-semibold text-zinc-200">Repository</h3>
            </div>
            <div className="p-5 space-y-5">
              {/* Repo Name */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <a
                    href={`https://github.com/${service.namespace}/${service.repo_name}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs font-medium uppercase tracking-wider text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1 cursor-pointer transition-colors w-fit"
                    title="Open Repository in GitHub"
                  >
                    Github Repository
                    <ExternalLink className="w-3 h-3" />
                  </a>
                </div>
                <div className="flex items-center gap-2 text-sm text-zinc-200 bg-zinc-950 px-3 py-2 rounded-lg border border-zinc-800/50 shadow-inner">
                  <Box className="w-4 h-4 text-zinc-500 flex-shrink-0" />
                  <CopyableText
                    text={`${service.namespace}/${service.repo_name}`}
                    variant="text"
                    className="text-zinc-200 font-mono"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4">
                {/* Branch */}
                <div>
                  <div className="flex items-center gap-2 mb-1.5">
                    <a
                      href={`https://github.com/${service.namespace}/${service.repo_name}/tree/${service.branch}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs font-medium uppercase tracking-wider text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1 cursor-pointer w-fit"
                      title="View Branch Tree"
                    >
                      Branch
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  </div>
                  <div className="text-sm font-mono text-zinc-300 bg-zinc-950/50 px-2 py-1.5 rounded border border-zinc-800/50">
                    <CopyableText text={service.branch} variant="id" className="text-zinc-300" />
                  </div>
                </div>

                {/* Commit SHA */}
                <div>
                  <div className="flex items-center gap-2 mb-1.5">
                    <a
                      href={`https://github.com/${service.namespace}/${service.repo_name}/commit/${service.commit_sha}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs font-medium uppercase tracking-wider text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1 cursor-pointer w-fit"
                      title="View Commit Details"
                    >
                      Commit SHA
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  </div>
                  <div className="text-sm font-mono text-zinc-400 bg-zinc-950/50 px-2 py-1.5 rounded border border-zinc-800/50 overflow-hidden">
                    <CopyableText text={service.commit_sha} copyValue={service.commit_sha} variant="id" />
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Resources */}
          <div className="shrink-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-2">
              <Cpu className="w-4 h-4 text-zinc-400" />
              <h3 className="text-sm font-semibold text-zinc-200">Resources</h3>
            </div>
            <div className="p-5 grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1.5">Accelerator</label>
                <span className="text-base text-white font-medium block">
                  {service.gpu_type === "CPU" ? "CPU Only" : service.gpu_type}
                </span>
              </div>
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1.5">GPU Count</label>
                <span className="text-base text-white font-medium block">{service.gpu_count} GPUs</span>
              </div>
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1.5">CPU Cores</label>
                <span className="text-base text-white font-medium block">
                  {service.cpu_count ? service.cpu_count : <span className="text-zinc-500 text-sm">(Station Default)</span>}
                </span>
              </div>
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1.5">Memory</label>
                <span className="text-base text-white font-medium block">
                  {service.memory_demand ? service.memory_demand : <span className="text-zinc-500 text-sm">(Station Default)</span>}
                </span>
              </div>
            </div>
          </div>

          {/* Entry Command */}
          <div className="flex-1 min-h-0 flex flex-col bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <div className="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Terminal className="w-4 h-4 text-zinc-400" />
                <h3 className="text-sm font-semibold text-zinc-200">Entry Command</h3>
              </div>
              <button
                onClick={() => copyToClipboard(service.entry_command, setCopiedCommand)}
                className="text-zinc-500 hover:text-zinc-200 transition-colors"
                title="Copy Full Command"
              >
                {copiedCommand ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
            <div className="flex-1 overflow-auto p-4 bg-zinc-950">
              <pre className="text-xs font-mono leading-relaxed whitespace-pre-wrap break-all selection:bg-green-900/50 selection:text-green-200">
                <span className="text-blue-400">export MAGNUS_PORT=&lt;available_port&gt;</span>
                {"\n"}
                <span className="text-green-400">{service.entry_command}</span>
              </pre>
            </div>
          </div>

        </div>
      </div>

      {/* Drawer */}
      <ServiceDrawer
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        initialData={service}
        onSuccess={handleDrawerSuccess}
      />

      {/* Confirmation Dialog */}
      <ConfirmationDialog
        isOpen={confirmOpen}
        onClose={() => !actionLoading && setConfirmOpen(false)}
        onConfirm={handleConfirmAction}
        title={dialogConfig.title}
        description={dialogConfig.description}
        confirmText={dialogConfig.confirmText}
        variant={dialogConfig.variant}
        isLoading={actionLoading}
      />
    </div>
  );
}

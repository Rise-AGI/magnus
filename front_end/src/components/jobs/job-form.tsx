// front_end/src/components/jobs/job-form.tsx
"use client";

import { useState, useEffect, useRef, useCallback, forwardRef, useImperativeHandle } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { NumberStepper } from "@/components/ui/number-stepper";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { CodeEditor } from "@/components/ui/code-editor";
import { client } from "@/lib/api";
import { JobCapabilities } from "@/types/job";
import { useLanguage } from "@/context/language-context";
import { CLUSTER_CONFIG, EXECUTION_TARGETS, DEFAULT_EXECUTION_TARGET, getExecutionTarget } from "@/lib/config";
import { ExecutionTargetSelect } from "@/components/jobs/execution-target-select";
import { useSearchParams } from "next/navigation";

const JOB_TYPES = [
  { label: "A1 - 高优稳定", value: "A1", meta: "Non-Preemptible • Urgent" },
  { label: "A2 - 次优稳定", value: "A2", meta: "Non-Preemptible" },
  { label: "B1 - 高优可抢", value: "B1", meta: "Preemptible (High)" },
  { label: "B2 - 次优可抢", value: "B2", meta: "Preemptible (Low)" },
];

interface Branch { name: string; commit_sha: string; }
interface Commit { sha: string; message: string; author: string; date: string; }

export interface JobFormData {
  execution_target?: string | null;
  taskName: string;
  description: string;
  namespace: string;
  repoName: string;
  branch: string;
  commit_sha: string;
  entry_command: string;
  gpu_count: number;
  gpu_type: string;
  job_type: string;
  cpu_count?: number | null;
  memory_demand?: string | null;
  node_count?: number | null;
  tasks_per_node?: number | null;
  time_limit?: number | null;
  ephemeral_storage?: string | null;
  runner?: string | null;
  container_image?: string | null;
  dockerfile?: string | null;
  system_entry_command?: string | null;
}

interface JobFormProps {
  mode: "create" | "clone";
  initialData?: JobFormData | null; 
  onCancel: () => void;
  onSuccess: () => void;
}

const JobForm = forwardRef(function JobForm({ mode, initialData, onCancel, onSuccess }: JobFormProps, ref) {
  const { t } = useLanguage();
  const searchParams = useSearchParams();
  const [executionTarget, setExecutionTarget] = useState(initialData?.execution_target ?? searchParams.get("execution_target") ?? DEFAULT_EXECUTION_TARGET);
  const target = getExecutionTarget(executionTarget);
  const config = target?.cluster ?? CLUSTER_CONFIG;
  const PHYSICAL_GPUS = config.gpus;
  const GPU_TYPES = [...PHYSICAL_GPUS, { label: "CPU Only", value: "cpu", meta: "Host Memory" }];
  const MAX_CPU_COUNT = config.max_cpu_count;
  const MAX_NODE_COUNT = config.max_node_count ?? 1;
  const DEFAULT_NODE_COUNT = config.default_node_count ?? 1;
  const DEFAULT_TASKS_PER_NODE = config.default_tasks_per_node ?? 1;
  const DEFAULT_MEMORY = config.default_memory_demand;
  const DEFAULT_EPHEMERAL_STORAGE = config.default_ephemeral_storage;
  const DEFAULT_CPU_COUNT = config.default_cpu_count;
  const DEFAULT_RUNNER = config.default_runner;
  const DEFAULT_CONTAINER_IMAGE = config.default_container_image;
  const DEFAULT_SYSTEM_ENTRY_COMMAND = config.default_system_entry_command;
  const IS_PER_CPU_MEMORY = config.mem_mode === "per_cpu";
  const MEM_PER_CPU_MB = config.mem_per_cpu_mb;
  const getGpuLimit = (type: string) => type === "cpu" ? 0 : (config.gpus.find(gpu => gpu.value === type)?.limit ?? 0);

  const [taskName, setTaskName] = useState(initialData?.taskName || "");
  const [description, setDescription] = useState(initialData?.description || "");
  const [namespace, setNamespace] = useState(initialData?.namespace || "Rise-AGI");
  const [repoName, setRepoName] = useState(initialData?.repoName || "");
  
  const [branches, setBranches] = useState<Branch[]>([]);
  const [commits, setCommits] = useState<Commit[]>([]);
  
  const [selectedBranch, setSelectedBranch] = useState(initialData?.branch || "");
  const [selectedCommit, setSelectedCommit] = useState(initialData?.commit_sha || "");
  const [command, setCommand] = useState(initialData?.entry_command || "");
  
  const [gpuCount, setGpuCount] = useState(initialData?.gpu_count ?? 0);
  const [gpuType, setGpuType] = useState(
    initialData?.gpu_type || (initialData?.gpu_count ? (PHYSICAL_GPUS[0]?.value ?? "cpu") : "cpu")
  );
  
  const [jobType, setJobType] = useState(initialData?.job_type || "A2");

  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [hasScanned, setHasScanned] = useState(false);
  
  const [errorField, setErrorField] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [repoError, setRepoError] = useState<string | null>(null);

  const [showAdvanced, setShowAdvanced] = useState(false);
  
  const [cpuCount, setCpuCount] = useState<number>(initialData?.cpu_count ?? 0);
  const [memoryDemand, setMemoryDemand] = useState<string>(initialData?.memory_demand || "");
  const [nodeCount, setNodeCount] = useState<number>(initialData?.node_count ?? DEFAULT_NODE_COUNT);
  const [tasksPerNode, setTasksPerNode] = useState<number>(initialData?.tasks_per_node ?? DEFAULT_TASKS_PER_NODE);
  const [timeLimit, setTimeLimit] = useState<number>(initialData?.time_limit ?? 0);
  const [ephemeralStorage, setEphemeralStorage] = useState<string>(initialData?.ephemeral_storage || "");
  const [runner, setRunner] = useState<string>(initialData?.runner || "");
  const [containerImage, setContainerImage] = useState<string>(initialData?.container_image || DEFAULT_CONTAINER_IMAGE);
  const [environmentSource, setEnvironmentSource] = useState<"image" | "dockerfile">(initialData?.dockerfile ? "dockerfile" : "image");
  const [dockerfile, setDockerfile] = useState(initialData?.dockerfile ?? "");
  const [capabilities, setCapabilities] = useState<JobCapabilities | null>(null);
  const [capabilitiesUnavailable, setCapabilitiesUnavailable] = useState(false);
  const [systemEntryCommand, setSystemEntryCommand] = useState<string>(initialData?.system_entry_command || DEFAULT_SYSTEM_ENTRY_COMMAND);

  const descriptionRef = useRef<HTMLTextAreaElement>(null);
  const commandRef = useRef<HTMLTextAreaElement>(null);
  const systemCommandRef = useRef<HTMLTextAreaElement>(null);
  const actionRef = useRef<HTMLDivElement>(null);
  const repoRequestRef = useRef(0);
  const submittingRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    client("/api/jobs/capabilities", { signal: controller.signal })
      .then((data: JobCapabilities) => { if (!controller.signal.aborted) setCapabilities(data); })
      .catch(() => { if (!controller.signal.aborted) setCapabilitiesUnavailable(true); });
    return () => controller.abort();
  }, []);

  useEffect(() => () => { repoRequestRef.current += 1; }, []);

  const resetRepository = () => {
    repoRequestRef.current += 1;
    setLoading(false);
    setHasScanned(false);
    setBranches([]);
    setCommits([]);
    setSelectedBranch("");
    setSelectedCommit("");
  };

  // === Imperative Handle for Clipboard Actions ===
  useImperativeHandle(ref, () => ({
    getPayload: () => {
      return {
        execution_target: executionTarget,
        task_name: taskName,
        description: description,
        namespace: namespace,
        repo_name: repoName,
        branch: selectedBranch,
        commit_sha: selectedCommit,
        entry_command: command,
        gpu_count: gpuCount,
        gpu_type: gpuType,
        job_type: jobType,
        cpu_count: cpuCount,
        memory_demand: memoryDemand,
        node_count: nodeCount,
        tasks_per_node: tasksPerNode,
        time_limit: timeLimit,
        ephemeral_storage: ephemeralStorage,
        runner: runner,
        ...(environmentSource === "dockerfile"
          ? { dockerfile }
          : { container_image: containerImage }),
        system_entry_command: systemEntryCommand,
      };
    },
    applyPayload: (payload: any) => {
      if (!payload) return;

      if (payload.dockerfile?.trim() && payload.container_image?.trim()) {
        setErrorMessage(t("jobForm.error.environmentConflict"));
        return;
      }

      if (payload.execution_target !== undefined) setExecutionTarget(payload.execution_target ?? DEFAULT_EXECUTION_TARGET);

      // Identity
      if (payload.task_name !== undefined) setTaskName(payload.task_name);
      else if (payload.name !== undefined) setTaskName(payload.name); // Compatibility

      if (payload.description !== undefined) setDescription(payload.description);

      // Git Source
      resetRepository();
      setNamespace(payload.namespace ?? "Rise-AGI");
      setRepoName(payload.repo_name ?? payload.repoName ?? "");

      setSelectedBranch(payload.branch ?? "");
      setSelectedCommit(payload.commit_sha ?? "");
      if (payload.entry_command !== undefined) setCommand(payload.entry_command);
      
      // Resources
      if (payload.gpu_count !== undefined) setGpuCount(payload.gpu_count);
      if (payload.gpu_type !== undefined) setGpuType(payload.gpu_type);
      if (payload.job_type !== undefined) setJobType(payload.job_type);
      
      // Advanced
      if (payload.cpu_count !== undefined) setCpuCount(payload.cpu_count);
      if (payload.memory_demand !== undefined) setMemoryDemand(payload.memory_demand ?? "");
      if (payload.node_count !== undefined && payload.node_count !== null) setNodeCount(payload.node_count);
      if (payload.tasks_per_node !== undefined && payload.tasks_per_node !== null) setTasksPerNode(payload.tasks_per_node);
      if (payload.time_limit !== undefined) setTimeLimit(payload.time_limit ?? 0);
      if (payload.ephemeral_storage !== undefined) setEphemeralStorage(payload.ephemeral_storage ?? "");
      if (payload.runner !== undefined) setRunner(payload.runner ?? "");
      if (payload.dockerfile != null) {
        setDockerfile(payload.dockerfile);
        setEnvironmentSource("dockerfile");
        setContainerImage(DEFAULT_CONTAINER_IMAGE);
      } else {
        setDockerfile("");
        setEnvironmentSource("image");
        setContainerImage(payload.container_image ?? DEFAULT_CONTAINER_IMAGE);
      }
      if (payload.system_entry_command !== undefined) setSystemEntryCommand(payload.system_entry_command ?? "");

      // Auto-scroll to actions
      setTimeout(() => {
        actionRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 100);
    }
  }));

  // Auto-resize for Description
  useEffect(() => {
    if (descriptionRef.current) {
      descriptionRef.current.style.height = 'auto';
      descriptionRef.current.style.height = `${descriptionRef.current.scrollHeight}px`;
    }
  }, [description]);

  // Auto-resize for Command
  useEffect(() => {
    if (commandRef.current) {
      commandRef.current.style.height = 'auto';
      commandRef.current.style.height = `${commandRef.current.scrollHeight}px`;
    }
  }, [command]);

  // Auto-resize for System Entry Command
  useEffect(() => {
    if (systemCommandRef.current) {
      systemCommandRef.current.style.height = 'auto';
      systemCommandRef.current.style.height = `${systemCommandRef.current.scrollHeight}px`;
    }
  }, [systemEntryCommand, showAdvanced]);

  const handleGpuTypeChange = (val: string) => {
    setGpuType(val);
    if (val === 'cpu') {
        setGpuCount(0);
    } else {
        if (gpuCount === 0) setGpuCount(1);
        const limit = getGpuLimit(val);
        if (gpuCount > limit) setGpuCount(limit);
    }
  };

  const clearError = (field: string) => {
    if (errorField === field) { setErrorField(null); setErrorMessage(null); }
  };

  const gitErrorMessage = useCallback((raw: string): string => {
    const map: Record<string, string> = {
      upstream_rate_limited: t("jobForm.error.upstreamRateLimited"),
      upstream_unreachable: t("jobForm.error.upstreamUnreachable"),
      upstream_timeout: t("jobForm.error.upstreamTimeout"),
      repo_not_found: t("jobForm.error.repoNotFound"),
      permission_denied: t("jobForm.error.permissionDenied"),
      git_error: t("jobForm.error.gitError"),
    };
    if (map[raw]) return map[raw];
    if (raw.toLowerCase().includes("timed out")) return t("jobForm.error.upstreamTimeout");
    return t("jobForm.error.gitError");
  }, [t]);

  const scrollToError = (id: string) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const fetchBranches = useCallback(async () => {
    const effectiveNamespace = namespace.trim() || "Rise-AGI";
    if (!repoName.trim() && effectiveNamespace !== "Rise-AGI") {
      setErrorField("repo"); setErrorMessage(t("jobForm.error.repoRequired")); return;
    }
    const request = ++repoRequestRef.current;
    
    setLoading(true);
    setErrorField(null); setErrorMessage(null);

    try {
      const data = await client(`/api/github/${encodeURIComponent(effectiveNamespace)}/${encodeURIComponent(repoName.trim() || "magnus")}/branches`);
      if (request !== repoRequestRef.current) return;
      setBranches(data);
      setHasScanned(true);
    } catch (e: any) {
      if (request !== repoRequestRef.current) return;
      console.error(e);
      setRepoError(gitErrorMessage(e.message || "git_error"));
      setHasScanned(false);
    } finally {
      if (request === repoRequestRef.current) setLoading(false);
    }
  }, [namespace, repoName, gitErrorMessage, t]);

  // Fetch commits when branch changes
  useEffect(() => {
    setCommits([]);
    if (!selectedBranch || !hasScanned || !branches.some(branch => branch.name === selectedBranch)) return;
    const controller = new AbortController();

    const fetchCommits = async () => {
      try {
        const data = await client(`/api/github/${encodeURIComponent(namespace.trim() || "Rise-AGI")}/${encodeURIComponent(repoName.trim() || "magnus")}/commits?branch=${encodeURIComponent(selectedBranch)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setCommits(data);
      } catch (e: any) {
        if (controller.signal.aborted) return;
        console.error("Fetch commits failed", e);
        setRepoError(gitErrorMessage(e.message || "git_error"));
      }
    };

    fetchCommits();
    return () => controller.abort();
  }, [selectedBranch, hasScanned, branches, namespace, repoName, gitErrorMessage]);

  const handleLaunch = async () => {
    if (submittingRef.current) return;
    setErrorField(null); setErrorMessage(null);

    if ((EXECUTION_TARGETS.length > 1 || initialData?.execution_target) && !capabilities?.execution_targets) { setErrorMessage(t("executionTarget.unavailable")); return; }
    if (!target) { setErrorMessage(t("executionTarget.unavailable")); return; }
    if (!taskName.trim()) { setErrorField("taskName"); setErrorMessage("⚠️ Task Name is required"); scrollToError("field-taskName"); return; }
    if (!repoName.trim() && namespace.trim() && namespace.trim() !== "Rise-AGI") { setErrorField("repo"); setErrorMessage(t("jobForm.error.repoRequired")); scrollToError("field-repo"); return; }
    if (!gpuType) { setErrorMessage("⚠️ Select GPU Type"); return; }
    if (!command.trim()) { setErrorField("command"); setErrorMessage("⚠️ Command required"); scrollToError("field-command"); return; }
    if (environmentSource === "dockerfile" && !dockerfile.trim()) { setErrorField("dockerfile"); setErrorMessage(t("jobForm.error.dockerfileRequired")); scrollToError("field-environment"); return; }

    const payload = {
      ...(EXECUTION_TARGETS.length > 1 || initialData?.execution_target ? { execution_target: executionTarget } : {}),
      task_name: taskName,
      description: description,
      namespace: namespace.trim() || "Rise-AGI",
      repo_name: repoName.trim() || null,
      branch: selectedBranch.trim() || null,
      commit_sha: selectedCommit.trim() || null,
      entry_command: command,
      gpu_count: gpuCount,
      gpu_type: gpuType,
      job_type: jobType,
      cpu_count: cpuCount ? cpuCount : null,
      // per_cpu 站点内存随 CPU 自动分配，不下发 memory_demand（后端按核数归一化）。
      memory_demand: IS_PER_CPU_MEMORY ? null : (memoryDemand.trim() ? memoryDemand.trim() : null),
      // 单节点(<=1)不下发,让后端回落默认 1 —— 保持单节点提交与历史一致。
      node_count: nodeCount > 1 ? nodeCount : null,
      tasks_per_node: tasksPerNode > 1 ? tasksPerNode : null,
      time_limit: timeLimit ? timeLimit : null,
      ephemeral_storage: ephemeralStorage.trim() ? ephemeralStorage.trim() : null,
      runner: runner.trim() ? runner.trim() : null,
      ...(environmentSource === "dockerfile"
        ? { dockerfile }
        : { container_image: containerImage.trim() || null }),
      system_entry_command: systemEntryCommand.trim() ? systemEntryCommand.trim() : null,
    };
    
    submittingRef.current = true;
    setSubmitting(true);
    try {
      if (environmentSource === "dockerfile") {
        // Older servers may ignore unknown fields; verify support before submitting.
        let currentCapabilities: JobCapabilities;
        try {
          currentCapabilities = await client("/api/jobs/capabilities");
        } catch {
          setCapabilitiesUnavailable(true);
          throw new Error(t("jobForm.buildUnavailable"));
        }
        setCapabilities(currentCapabilities);
        setCapabilitiesUnavailable(false);
        if (!currentCapabilities.dockerfile || !currentCapabilities.image_build_enabled) {
          throw new Error(t("jobForm.buildDisabled"));
        }
      }
      await client("/api/jobs/submit", { json: payload });
      onSuccess(); 
    } catch (e: any) {
      console.error(e);
      setErrorMessage(e.message || "Submit Failed");
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col gap-8">

      <ExecutionTargetSelect
        value={executionTarget}
        onChange={id => {
          setExecutionTarget(id);
          const next = getExecutionTarget(id)?.cluster;
          if (!next) return;
          setCpuCount(0);
          setMemoryDemand("");
          setNodeCount(next.default_node_count ?? 1);
          setTasksPerNode(next.default_tasks_per_node ?? 1);
          setGpuType("cpu");
          setGpuCount(0);
          if (runner === DEFAULT_RUNNER) setRunner("");
          if (containerImage === DEFAULT_CONTAINER_IMAGE) setContainerImage(next.default_container_image);
          if (systemEntryCommand === DEFAULT_SYSTEM_ENTRY_COMMAND) setSystemEntryCommand(next.default_system_entry_command);
        }}
        disabled={submitting}
      />
      {/* Task Info */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            {t("jobForm.taskInfo")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>

        <div className="mb-4" id="field-taskName">
          <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'taskName' ? 'text-red-500' : 'text-zinc-500'}`}>
            {t("jobForm.taskName")} <span className="text-red-500">*</span>
          </label>
          <input 
            className={`w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
              ${errorField === 'taskName' ? 'animate-shake border-red-500' : 'border-zinc-800'}`} 
            value={taskName} 
            placeholder="e.g. ResNet50-Baseline-v1"
            onChange={e => { setTaskName(e.target.value); clearError('taskName'); }} 
          />
        </div>

        <div className="mb-4">
          <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
            {t("jobForm.description")} <span className="text-zinc-600 normal-case ml-1">({t("common.optional")})</span>
          </label>
          <textarea 
            ref={descriptionRef}
            className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500/50 focus:shadow-[0_0_15px_rgba(59,130,246,0.1)] outline-none transition-all placeholder-zinc-700 resize-none overflow-hidden min-h-[42px]"
            value={description} 
            placeholder="Brief description of this experiment..."
            rows={1}
            onChange={e => setDescription(e.target.value)} 
          />
        </div>
      </div>
      
      {/* Code Source */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            {t("jobForm.codeSource")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>

        <div className="grid grid-cols-2 gap-4 mb-4">
          <div id="field-namespace">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'namespace' ? 'text-red-500' : 'text-zinc-500'}`}>{t("jobForm.namespace")}</label>
            <input 
              className={`w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                ${errorField === 'namespace' ? 'animate-shake border-red-500' : 'border-zinc-800'}`} 
              value={namespace} 
              placeholder="e.g. Rise-AGI"
              onChange={e => { setNamespace(e.target.value); resetRepository(); clearError('namespace'); }}
            />
          </div>
          <div id="field-repo">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'repo' ? 'text-red-500' : 'text-zinc-500'}`}>{t("jobForm.repoName")}</label>
            <input 
              className={`w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                ${errorField === 'repo' ? 'animate-shake border-red-500' : 'border-zinc-800'}`} 
              value={repoName} 
              placeholder="e.g. magnus"
              onChange={e => { setRepoName(e.target.value); resetRepository(); clearError('repo'); }}
            />
          </div>
        </div>
        <p className="text-xs text-zinc-500 mb-3">{t("jobForm.defaultRepository")}</p>
        <button
            onClick={fetchBranches}
            disabled={loading}
            className="w-full bg-zinc-900 hover:bg-zinc-800 text-zinc-300 py-2.5 rounded-lg text-sm font-medium transition-all active:scale-[0.98] disabled:opacity-50 mb-6 border border-zinc-800 flex justify-center items-center gap-2"
        >
            {loading ? (
                <>
                  <span className="w-4 h-4 border-2 border-zinc-500 border-t-white rounded-full animate-spin"></span>
                  {t("jobForm.scanning")}
                </>
            ) : t("jobForm.scanRepo")}
        </button>

        <div className="grid grid-cols-1 gap-0">
          <SearchableSelect
            id="field-branch" label={t("jobForm.branch")} placeholder={t("jobForm.defaultBranch")} className="mb-4"
            value={selectedBranch} allowCustomValue
            onChange={value => { setSelectedBranch(value); setSelectedCommit(""); clearError("branch"); }}
            options={branches.map(branch => ({ label: branch.name, value: branch.name }))}
          />
          <SearchableSelect
            id="field-commit" label={t("jobForm.commit")} placeholder="HEAD" className="mb-4"
            value={selectedCommit} allowCustomValue
            onChange={value => { setSelectedCommit(value); clearError("commit"); }}
            options={[
              { label: t("jobForm.latestCommit"), value: "HEAD", meta: t("jobForm.useLatestCode") },
              ...commits.map(commit => ({ label: commit.message, value: commit.sha, meta: `${commit.sha.substring(0, 7)} • ${commit.author}` })),
            ]}
          />
        </div>
      </div>

      <div id="field-environment">
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
          {t("jobForm.environment")}
          <div className="h-px bg-zinc-800 flex-grow ml-2" />
        </h3>
        <div className="flex gap-2 mb-3" role="group" aria-label={t("jobForm.environment")}>
          {(["image", "dockerfile"] as const).map(source => (
            <button
              key={source} type="button" aria-pressed={environmentSource === source}
              onClick={() => { setEnvironmentSource(source); clearError("dockerfile"); }}
              className={`px-3 py-2 rounded-lg text-sm border transition-colors ${environmentSource === source ? "bg-blue-500/10 text-blue-400 border-blue-500/40" : "border-zinc-800 text-zinc-400 hover:text-zinc-200"}`}
            >
              {source === "image" ? t("jobForm.containerImage") : "Dockerfile"}
            </button>
          ))}
        </div>
        {environmentSource === "image" ? (
          <input
            aria-label={t("jobForm.containerImage")}
            className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none placeholder-zinc-700 font-mono"
            value={containerImage}
            placeholder={t("jobForm.containerImageDefault", { value: DEFAULT_CONTAINER_IMAGE })}
            onChange={e => setContainerImage(e.target.value)}
          />
        ) : (
          <>
            <div className={`rounded-lg border bg-zinc-950 overflow-auto max-h-[400px] ${errorField === "dockerfile" ? "border-red-500" : "border-zinc-800"}`}>
              <CodeEditor filename="Dockerfile" value={dockerfile} onChange={value => { setDockerfile(value); clearError("dockerfile"); }} minHeight="180px" />
            </div>
            <p className="text-xs text-zinc-500 mt-2">{t("jobForm.dockerfileHint")}</p>
            {(capabilitiesUnavailable || (capabilities && (!capabilities.dockerfile || !capabilities.image_build_enabled))) && (
              <p className="text-xs text-amber-400 mt-2" role="status">
                {t(capabilitiesUnavailable ? "jobForm.buildUnavailable" : "jobForm.buildDisabled")}
              </p>
            )}
          </>
        )}
      </div>

      {/* Job Scheduling */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            {t("jobForm.scheduling")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>

        <SearchableSelect
            label={t("jobForm.priority")}
            value={jobType}
            onChange={setJobType}
            options={JOB_TYPES}
            placeholder="Select Priority..."
            className="mb-0"
        />
      </div>

      {/* Compute Resources */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            {t("jobForm.computeResources")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
          {/* CPU Count */}
          <div>
            <NumberStepper
              label={t("jobForm.cpuCores")}
              value={cpuCount}
              onChange={setCpuCount}
              min={0}
              max={MAX_CPU_COUNT}
            />
            <p className="text-[11px] text-zinc-500 mt-1.5 ml-0.5">
              {t("jobForm.cpuCoresHint", { value: DEFAULT_CPU_COUNT.toString() })}
            </p>
          </div>

          {/* Memory */}
          <div>
            <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
              {t("jobForm.memory")}
            </label>
            {IS_PER_CPU_MEMORY ? (
              // per_cpu 站点禁用 --mem，内存按「核数 × 每核内存」由站点自动分配，
              // 不可独立调整：展示随 CPU 实时变化的派生值 + 每核内存提示，提交时
              // 不下发 memory_demand（后端归一化为真实分配值）。
              <>
                <div className="w-full bg-zinc-900/60 border border-zinc-800 px-3 py-2.5 rounded-lg text-zinc-400 text-sm select-none">
                  {(cpuCount || DEFAULT_CPU_COUNT) * MEM_PER_CPU_MB}M
                </div>
                <p className="text-[11px] text-zinc-500 mt-1.5 ml-0.5">
                  {t("jobForm.memoryPerCpuHint", { value: `${MEM_PER_CPU_MB}M` })}
                </p>
              </>
            ) : (
              <input
                type="text"
                className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700"
                value={memoryDemand}
                placeholder={t("jobForm.memoryDefault", { value: DEFAULT_MEMORY })}
                onChange={e => setMemoryDemand(e.target.value)}
              />
            )}
          </div>
        </div>

        {MAX_NODE_COUNT > 1 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
            {/* Node Count (multi-node MPI; only shown where the site enables it) */}
            <div>
              <NumberStepper
                label={t("jobForm.nodeCount")}
                value={nodeCount}
                onChange={setNodeCount}
                min={1}
                max={MAX_NODE_COUNT}
              />
              <p className="text-[11px] text-zinc-500 mt-1.5 ml-0.5">
                {t("jobForm.nodeCountHint")}
              </p>
            </div>
            {/* Tasks per node (MPI ranks per node) */}
            <div>
              <NumberStepper
                label={t("jobForm.tasksPerNode")}
                value={tasksPerNode}
                onChange={setTasksPerNode}
                min={1}
                max={MAX_CPU_COUNT}
              />
              <p className="text-[11px] text-zinc-500 mt-1.5 ml-0.5">
                {t("jobForm.tasksPerNodeHint")}
              </p>
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 gap-4">
            <SearchableSelect
                label={t("jobForm.gpuAccelerator")}
                value={gpuType}
                onChange={handleGpuTypeChange}
                options={GPU_TYPES}
                placeholder="Select GPU model..."
                className="mb-4"
            />
            <NumberStepper
                label={t("jobForm.gpuCount")}
                value={gpuCount}
                onChange={setGpuCount}
                min={0}
                max={getGpuLimit(gpuType)}
                disabled={gpuType === 'cpu'}
            />
        </div>


        {/* Advanced Options (Collapsible) */}
        <div className="pt-2">
          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="flex items-center gap-2 text-sm font-medium text-zinc-400 hover:text-zinc-200 transition-colors select-none group"
          >
            <div className="text-zinc-600 group-hover:text-zinc-300 transition-colors">
              {showAdvanced ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </div>
            <span>{t("common.advanced")}</span>
          </button>

          {showAdvanced && (
            <div className="mt-3 pl-1 grid grid-cols-1 sm:grid-cols-2 gap-4 animate-in slide-in-from-top-1 duration-200">

              {/* Max Runtime (time limit, minutes) */}
              <div className="sm:col-span-2">
                <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
                  {t("jobForm.timeLimit")}
                </label>
                <input
                  type="text"
                  className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700"
                  value={timeLimit || ""}
                  placeholder={t("jobForm.timeLimitHint")}
                  onChange={e => setTimeLimit(parseInt(e.target.value, 10) || 0)}
                />
              </div>

              {/* Ephemeral Storage */}
              <div className="sm:col-span-2">
                <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
                  {t("jobForm.ephemeralStorage")}
                </label>
                <input
                  type="text"
                  className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700"
                  value={ephemeralStorage}
                  placeholder={t("jobForm.ephemeralStorageDefault", { value: DEFAULT_EPHEMERAL_STORAGE })}
                  onChange={e => setEphemeralStorage(e.target.value)}
                />
              </div>

              {/* Runner Override */}
              <div className="sm:col-span-2">
                <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
                  {t("jobForm.runAsUser")}
                </label>
                <div className="relative">
                    <input
                    type="text"
                    className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700 font-mono"
                    value={runner}
                    placeholder={t("jobForm.runAsUserDefault", { value: DEFAULT_RUNNER })}
                    onChange={e => setRunner(e.target.value)}
                  />
                </div>
              </div>

              {/* System Entry Command Override */}
              <div className="sm:col-span-2">
                <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
                  {t("jobForm.systemEntryCommand")}
                </label>
                <div className="relative group">
                  <span className="absolute left-3 top-3 text-zinc-600 select-none font-mono text-sm">$</span>
                  <textarea
                    ref={systemCommandRef}
                    className="w-full bg-zinc-950 border border-zinc-800 px-3 pl-7 py-3 rounded-lg text-green-400 font-mono text-sm focus:border-green-500/50 focus:shadow-[0_0_15px_rgba(34,197,94,0.1)] outline-none shadow-inner min-h-[100px] leading-relaxed placeholder-zinc-800 resize-none overflow-hidden"
                    value={systemEntryCommand}
                    placeholder={t("jobForm.systemEntryCommandDefault")}
                    onChange={e => setSystemEntryCommand(e.target.value)}
                    spellCheck={false}
                  />
                </div>
              </div>
            </div>
          )}
        </div>

      </div>

      {/* Execution */}
      <div id="field-command">
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            {t("jobForm.execution")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'command' ? 'text-red-500' : 'text-zinc-500'}`}>{t("jobForm.entryCommand")}</label>
        <div className="relative group">
            <span className="absolute left-3 top-3 text-zinc-600 select-none font-mono text-sm">$</span>
            <textarea 
                ref={commandRef}
                className={`w-full bg-zinc-950 border px-3 pl-7 py-3 rounded-lg text-green-400 font-mono text-sm focus:border-green-500/50 focus:shadow-[0_0_15px_rgba(34,197,94,0.1)] outline-none shadow-inner min-h-[100px] leading-relaxed placeholder-zinc-800 resize-none overflow-hidden
                ${errorField === 'command' ? 'animate-shake border-red-500' : 'border-zinc-800'}`}
                value={command} 
                placeholder="python train.py ..."
                onChange={e => { setCommand(e.target.value); clearError('command'); }}
                spellCheck={false}
            />
        </div>
      </div>
      
      <div ref={actionRef} className="mt-4 pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4">
        {errorMessage ? (
             <span className="text-red-500 text-xs font-bold animate-pulse text-center sm:text-left">{errorMessage}</span>
        ) : (
            <span className="text-zinc-500 text-xs text-center sm:text-left hidden sm:block">{t("jobForm.waitingForLaunch")}</span>
        )}

        <div className="flex gap-3 w-full sm:w-auto">
            <button
                onClick={onCancel}
                className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
            >
                {t("common.cancel")}
            </button>
            <button
                onClick={handleLaunch}
                disabled={submitting}
                className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-wait"
            >
                {mode === 'create' ? t("jobForm.launchJob") : t("jobForm.reLaunch")}
            </button>
        </div>
      </div>

      <ConfirmationDialog
        isOpen={!!repoError}
        onClose={() => setRepoError(null)}
        title={t("common.error")}
        description={repoError}
        confirmText={t("common.ok")}
        mode="alert"
        variant="danger"
      />
    </div>
  );
});

export default JobForm;

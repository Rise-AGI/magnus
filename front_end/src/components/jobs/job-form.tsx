// front_end/src/components/jobs/job-form.tsx
"use client";

import { useState, useEffect, useRef, useCallback, forwardRef, useImperativeHandle } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { NumberStepper } from "@/components/ui/number-stepper";
import { client } from "@/lib/api";
import { useLanguage } from "@/context/language-context";
import {
  PHYSICAL_GPUS,
  getGpuLimit,
  MAX_CPU_COUNT,
  DEFAULT_MEMORY,
  DEFAULT_EPHEMERAL_STORAGE,
  DEFAULT_RUNNER,
  DEFAULT_CONTAINER_IMAGE,
  DEFAULT_SYSTEM_ENTRY_COMMAND,
} from "@/lib/config";

const GPU_TYPES = [
  ...PHYSICAL_GPUS,
  { label: "CPU Only", value: "cpu", meta: "Host Memory" },
];

const JOB_TYPES = [
  { label: "A1 - 高优稳定", value: "A1", meta: "Non-Preemptible • Urgent" },
  { label: "A2 - 次优稳定", value: "A2", meta: "Non-Preemptible" },
  { label: "B1 - 高优可抢", value: "B1", meta: "Preemptible (High)" },
  { label: "B2 - 次优可抢", value: "B2", meta: "Preemptible (Low)" },
];

interface Branch { name: string; commit_sha: string; }
interface Commit { sha: string; message: string; author: string; date: string; }

export interface JobFormData {
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
  ephemeral_storage?: string | null;
  runner?: string | null;
  container_image?: string | null;
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
    initialData?.gpu_type || (initialData?.gpu_count ? PHYSICAL_GPUS[0].value : "cpu")
  );
  
  const [jobType, setJobType] = useState(initialData?.job_type || "A2");

  const [loading, setLoading] = useState(false);
  const [hasScanned, setHasScanned] = useState(false);
  
  const [errorField, setErrorField] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [showAdvanced, setShowAdvanced] = useState(false);
  
  const [cpuCount, setCpuCount] = useState<number>(initialData?.cpu_count ?? 0);
  const [memoryDemand, setMemoryDemand] = useState<string>(initialData?.memory_demand || "");
  const [ephemeralStorage, setEphemeralStorage] = useState<string>(initialData?.ephemeral_storage || "");
  const [runner, setRunner] = useState<string>(initialData?.runner || "");
  const [containerImage, setContainerImage] = useState<string>(initialData?.container_image || DEFAULT_CONTAINER_IMAGE);
  const [systemEntryCommand, setSystemEntryCommand] = useState<string>(initialData?.system_entry_command || DEFAULT_SYSTEM_ENTRY_COMMAND);

  const descriptionRef = useRef<HTMLTextAreaElement>(null);
  const commandRef = useRef<HTMLTextAreaElement>(null);
  const systemCommandRef = useRef<HTMLTextAreaElement>(null);
  const actionRef = useRef<HTMLDivElement>(null);

  // === Imperative Handle for Clipboard Actions ===
  useImperativeHandle(ref, () => ({
    getPayload: () => {
      return {
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
        ephemeral_storage: ephemeralStorage,
        runner: runner,
        container_image: containerImage,
        // system_entry_command 不序列化，跨环境复制时用默认值更安全
      };
    },
    applyPayload: (payload: any) => {
      if (!payload) return;

      // Identity
      if (payload.task_name !== undefined) setTaskName(payload.task_name);
      else if (payload.name !== undefined) setTaskName(payload.name); // Compatibility

      if (payload.description !== undefined) setDescription(payload.description);

      // Git Source
      if (payload.namespace !== undefined) setNamespace(payload.namespace);
      if (payload.repo_name !== undefined) setRepoName(payload.repo_name);
      else if (payload.repoName !== undefined) setRepoName(payload.repoName);

      if (payload.branch !== undefined) setSelectedBranch(payload.branch);
      if (payload.commit_sha !== undefined) setSelectedCommit(payload.commit_sha);
      if (payload.entry_command !== undefined) setCommand(payload.entry_command);
      
      // Bypass scan requirement if software info is already present
      if (payload.namespace && (payload.repo_name || payload.repoName) 
        && payload.branch && payload.commit_sha) {
        setHasScanned(true);
      }

      // Resources
      if (payload.gpu_count !== undefined) setGpuCount(payload.gpu_count);
      if (payload.gpu_type !== undefined) setGpuType(payload.gpu_type);
      if (payload.job_type !== undefined) setJobType(payload.job_type);
      
      // Advanced
      if (payload.cpu_count !== undefined) setCpuCount(payload.cpu_count);
      if (payload.memory_demand !== undefined) setMemoryDemand(payload.memory_demand);
      if (payload.ephemeral_storage !== undefined) setEphemeralStorage(payload.ephemeral_storage);
      if (payload.runner !== undefined) setRunner(payload.runner);
      if (payload.container_image !== undefined) setContainerImage(payload.container_image);
      if (payload.system_entry_command !== undefined) setSystemEntryCommand(payload.system_entry_command);

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

  const scrollToError = (id: string) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const fetchBranches = useCallback(async () => {
    if (!namespace.trim()) { setErrorField("namespace"); setErrorMessage("⚠️ Namespace is required"); return; }
    if (!repoName.trim()) { setErrorField("repo"); setErrorMessage("⚠️ Repo Name is required"); return; }
    
    setLoading(true);
    setErrorField(null); setErrorMessage(null);

    if (mode === 'create') {
        setBranches([]); setCommits([]); setSelectedBranch(""); setSelectedCommit(""); 
    }
    
    try {
      const data = await client(`/api/github/${namespace}/${repoName}/branches`);
      setBranches(data);
      setHasScanned(true);
      if (mode === 'create' && data.length > 0) {
        setSelectedBranch(data[0].name);
      }
    } catch (e: any) {
      console.error(e);
      setErrorMessage(e.message || "Failed to fetch branches");
      setHasScanned(false);
    } finally { 
      setLoading(false); 
    }
  }, [namespace, repoName, mode]);

  // Init for Clone Mode
  useEffect(() => {
    if (mode === 'clone' && initialData) {
        setHasScanned(true); 
        fetchBranches();
    }
  }, [mode, initialData, fetchBranches]); 

  // Fetch commits when branch changes
  useEffect(() => {
    if (!selectedBranch || !hasScanned) return;

    const fetchCommits = async () => {
      try {
        const data = await client(`/api/github/${namespace}/${repoName}/commits?branch=${selectedBranch}`);
        setCommits(data);
        if (mode === 'create' && data.length > 0) {
            setSelectedCommit("HEAD");
        }
      } catch (e) { 
        console.error("Fetch commits failed", e); 
      }
    };
    
    fetchCommits();
  }, [selectedBranch, hasScanned, namespace, repoName, mode]);

  const handleLaunch = async () => {
    setErrorField(null); setErrorMessage(null);

    if (!taskName.trim()) { setErrorField("taskName"); setErrorMessage("⚠️ Task Name is required"); scrollToError("field-taskName"); return; }
    if (!namespace.trim()) { setErrorField("namespace"); setErrorMessage("⚠️ Namespace required"); scrollToError("field-namespace"); return; }
    if (!repoName.trim()) { setErrorField("repo"); setErrorMessage("⚠️ Repo required"); scrollToError("field-repo"); return; }
    if (!hasScanned) { setErrorField("repo"); setErrorMessage("⚠️ Please Scan Repo first"); scrollToError("field-repo"); return; }
    if (!selectedBranch) { setErrorField("branch"); setErrorMessage("⚠️ Select Branch"); scrollToError("field-branch"); return; }
    if (!selectedCommit) { setErrorField("commit"); setErrorMessage("⚠️ Select Commit"); scrollToError("field-commit"); return; }
    if (!gpuType) { setErrorMessage("⚠️ Select GPU Type"); return; }
    if (!command.trim()) { setErrorField("command"); setErrorMessage("⚠️ Command required"); scrollToError("field-command"); return; }

    const payload = {
      task_name: taskName,
      description: description,
      namespace,
      repo_name: repoName,
      branch: selectedBranch,
      commit_sha: selectedCommit,
      entry_command: command,
      gpu_count: gpuCount,
      gpu_type: gpuType,
      job_type: jobType,
      cpu_count: cpuCount ? cpuCount : null,
      memory_demand: memoryDemand.trim() ? memoryDemand.trim() : null,
      ephemeral_storage: ephemeralStorage.trim() ? ephemeralStorage.trim() : null,
      runner: runner.trim() ? runner.trim() : null,
      container_image: containerImage.trim() ? containerImage.trim() : null,
      system_entry_command: systemEntryCommand.trim() ? systemEntryCommand.trim() : null,
    };
    
    try {
      await client("/api/jobs/submit", { json: payload });
      onSuccess(); 
    } catch (e: any) {
      console.error(e);
      setErrorMessage(e.message || "Submit Failed");
    }
  };

  return (
    <div className="flex flex-col gap-8">

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
              onChange={e => { setNamespace(e.target.value); clearError('namespace'); }} 
            />
          </div>
          <div id="field-repo">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'repo' ? 'text-red-500' : 'text-zinc-500'}`}>{t("jobForm.repoName")}</label>
            <input 
              className={`w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                ${errorField === 'repo' ? 'animate-shake border-red-500' : 'border-zinc-800'}`} 
              value={repoName} 
              placeholder="e.g. magnus"
              onChange={e => { setRepoName(e.target.value); clearError('repo'); }} 
            />
          </div>
        </div>
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
            id="field-branch" label={t("jobForm.branch")} disabled={!hasScanned} placeholder="Select branch..." className="mb-4"
            value={selectedBranch} onChange={(v) => { setSelectedBranch(v); clearError('branch'); }}
            options={branches.map(b => ({ label: b.name, value: b.name }))}
            hasError={errorField === 'branch'}
          />
          <SearchableSelect
            id="field-commit" label={t("jobForm.commit")} disabled={!hasScanned} placeholder="Select commit..." className="mb-4"
            value={selectedCommit} onChange={(v) => { setSelectedCommit(v); clearError('commit'); }}
            options={[
              {
                label: t("jobForm.latestCommit"),
                value: "HEAD",
                meta: t("jobForm.useLatestCode")
              },
              ...commits.map(c => ({
                label: c.message,
                value: c.sha,
                meta: `${c.sha.substring(0, 7)} • ${c.author}`
              }))
            ]}
            hasError={errorField === 'commit'}
          />
        </div>
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

              {/* CPU Count Override */}
              <div>
                <NumberStepper
                  label={t("jobForm.cpuCores")}
                  value={cpuCount}
                  onChange={setCpuCount}
                  min={0}
                  max={MAX_CPU_COUNT}
                />
                <p className="text-[11px] text-zinc-500 mt-1.5 ml-0.5">
                  {t("jobForm.cpuCoresHint")}
                </p>
              </div>

              {/* Memory Override */}
              <div>
                <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
                  {t("jobForm.memory")}
                </label>
                <input
                  type="text"
                  className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700"
                  value={memoryDemand}
                  placeholder={t("jobForm.memoryDefault", { value: DEFAULT_MEMORY })}
                  onChange={e => setMemoryDemand(e.target.value)}
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

              {/* Container Image Override */}
              <div className="sm:col-span-2">
                <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
                  {t("jobForm.containerImage")}
                </label>
                <div className="relative">
                    <input
                    type="text"
                    className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700 font-mono"
                    value={containerImage}
                    placeholder={t("jobForm.containerImageDefault", { value: DEFAULT_CONTAINER_IMAGE })}
                    onChange={e => setContainerImage(e.target.value)}
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
                className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2"
            >
                {mode === 'create' ? t("jobForm.launchJob") : t("jobForm.reLaunch")}
            </button>
        </div>
      </div>

    </div>
  );
});

export default JobForm;
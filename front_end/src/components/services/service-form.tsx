// front_end/src/components/services/service-form.tsx
"use client";

import { useState, useEffect, useRef, useCallback, forwardRef, useImperativeHandle } from "react";
import { ChevronDown, ChevronRight, Layers } from "lucide-react";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { NumberStepper } from "@/components/ui/number-stepper";
import { client } from "@/lib/api";
import { Service } from "@/types/service";
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
import { ServiceImplicitExport } from "@/lib/service-defaults";

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

export interface ServiceFormData {
  id: string; 
  name: string;
  description?: string | null; 
  
  request_timeout: number;
  idle_timeout: number;
  max_concurrency: number;

  namespace: string;
  repo_name: string;
  branch: string;
  commit_sha: string;
  entry_command: string;
  
  // Job Metadata
  job_task_name: string;
  job_description: string;

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

interface ServiceFormProps {
  initialData?: ServiceFormData | Service | null; 
  onCancel: () => void;
  onSuccess: () => void;
}

const ServiceForm = forwardRef(function ServiceForm({ initialData, onCancel, onSuccess }: ServiceFormProps, ref) {
  const { t } = useLanguage();
  const data = initialData as ServiceFormData; 

  // === Service Identity ===
  const [serviceId, setServiceId] = useState(data?.id || "");
  const [name, setName] = useState(data?.name || "");
  const [description, setDescription] = useState(data?.description || "");
  
  // === Service Policies ===
  const [requestTimeout, setRequestTimeout] = useState(data?.request_timeout ?? 60);
  const [idleTimeout, setIdleTimeout] = useState(data?.idle_timeout ?? 30);
  const [maxConcurrency, setMaxConcurrency] = useState(data?.max_concurrency ?? 50);

  // === Job Identity ===
  const [jobTaskName, setJobTaskName] = useState(data?.job_task_name || "");
  const [jobDescription, setJobDescription] = useState(data?.job_description || "");

  // === Code Source ===
  const [namespace, setNamespace] = useState(data?.namespace || "Rise-AGI");
  const [repoName, setRepoName] = useState(data?.repo_name || "");
  
  const [branches, setBranches] = useState<Branch[]>([]);
  const [commits, setCommits] = useState<Commit[]>([]);
  
  const [selectedBranch, setSelectedBranch] = useState(data?.branch || "");
  const [selectedCommit, setSelectedCommit] = useState(data?.commit_sha || "HEAD");
  const [command, setCommand] = useState(data?.entry_command || "");
  
  // === Resources ===
  const [gpuCount, setGpuCount] = useState(data?.gpu_count ?? 0);
  const [gpuType, setGpuType] = useState(
    data?.gpu_type || (data?.gpu_count === 0 ? "cpu" : PHYSICAL_GPUS[0].value)
  ); 
  const [jobType, setJobType] = useState(data?.job_type || "A2");

  // === UI States ===
  const [loading, setLoading] = useState(false);
  const [hasScanned, setHasScanned] = useState(false);
  const [errorField, setErrorField] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // === Advanced Overrides ===
  const [cpuCount, setCpuCount] = useState<number>(0);
  const [memoryDemand, setMemoryDemand] = useState<string>(data?.memory_demand || "");
  const [ephemeralStorage, setEphemeralStorage] = useState<string>(data?.ephemeral_storage || "");
  const [runner, setRunner] = useState<string>(data?.runner || "");
  const [containerImage, setContainerImage] = useState<string>(data?.container_image || DEFAULT_CONTAINER_IMAGE);
  const [systemEntryCommand, setSystemEntryCommand] = useState<string>(data?.system_entry_command || DEFAULT_SYSTEM_ENTRY_COMMAND);

  const actionRef = useRef<HTMLDivElement>(null);

  // === Expose Methods for ConfigClipboard ===
  useImperativeHandle(ref, () => ({
    getPayload: () => {
      return {
        id: serviceId,
        name,
        description,
        request_timeout: requestTimeout,
        idle_timeout: idleTimeout,
        max_concurrency: maxConcurrency,
        namespace,
        repo_name: repoName,
        branch: selectedBranch,
        commit_sha: selectedCommit,
        entry_command: command,
        job_task_name: jobTaskName,
        job_description: jobDescription,
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
      if (payload.id !== undefined) setServiceId(payload.id);
      if (payload.name !== undefined) setName(payload.name);
      if (payload.description !== undefined) setDescription(payload.description);
      
      // Policies
      if (payload.request_timeout !== undefined) setRequestTimeout(payload.request_timeout);
      if (payload.idle_timeout !== undefined) setIdleTimeout(payload.idle_timeout);
      if (payload.max_concurrency !== undefined) setMaxConcurrency(payload.max_concurrency);

      // Job Identity
      if (payload.job_task_name !== undefined) setJobTaskName(payload.job_task_name);
      if (payload.job_description !== undefined) setJobDescription(payload.job_description);

      // Code
      if (payload.namespace !== undefined) setNamespace(payload.namespace);
      if (payload.repo_name !== undefined) setRepoName(payload.repo_name);
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

      setTimeout(() => {
        actionRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 100);
    }
  }));

  // Refs for auto-resize textareas
  const jobDescriptionRef = useRef<HTMLTextAreaElement>(null);
  const commandRef = useRef<HTMLTextAreaElement>(null);
  const systemCommandRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize for Job Description
  useEffect(() => {
    if (jobDescriptionRef.current) {
      jobDescriptionRef.current.style.height = 'auto';
      jobDescriptionRef.current.style.height = `${jobDescriptionRef.current.scrollHeight}px`;
    }
  }, [jobDescription]);

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

  // Sync Service Name -> Job Task Name
  const handleServiceNameChange = (val: string) => {
    setName(val);
    clearError('name');
  };

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

  const clearError = (field: string) => { if (errorField === field) { setErrorField(null); setErrorMessage(null); } };
  const scrollToError = (id: string) => { const el = document.getElementById(id); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' }); };

  const fetchBranches = useCallback(async () => {
    if (!namespace.trim()) { setErrorField("namespace"); setErrorMessage("⚠️ Namespace required"); return; }
    if (!repoName.trim()) { setErrorField("repo"); setErrorMessage("⚠️ Repo required"); return; }
    setLoading(true); setErrorField(null); setErrorMessage(null);
    if (!initialData) { setBranches([]); setCommits([]); setSelectedBranch(""); setSelectedCommit(""); } 
    
    try {
      const data = await client(`/api/github/${namespace}/${repoName}/branches`);
      setBranches(data);
      setHasScanned(true);
      if (!initialData && data.length > 0) setSelectedBranch(data[0].name);
    } catch (e: any) { setErrorMessage(e.message); setHasScanned(false); } finally { setLoading(false); }
  }, [namespace, repoName, initialData]);

  useEffect(() => {
    if (initialData) { setHasScanned(true); fetchBranches(); }
  }, [initialData, fetchBranches]); 

  useEffect(() => {
    if (!selectedBranch || !hasScanned) return;
    const fetchCommits = async () => {
      try {
        const data = await client(`/api/github/${namespace}/${repoName}/commits?branch=${selectedBranch}`);
        setCommits(data);
        if (!initialData && data.length > 0) setSelectedCommit(data[0].sha);
      } catch (e) { console.error(e); }
    };
    fetchCommits();
  }, [selectedBranch, hasScanned, namespace, repoName, initialData]);

  const handleSave = async () => {
    setErrorField(null); setErrorMessage(null);

    // Validations
    if (!name.trim()) { setErrorField("name"); setErrorMessage("⚠️ Service Name is required"); scrollToError("field-name"); return; }
    if (!serviceId.trim()) { setErrorField("serviceId"); setErrorMessage("⚠️ Service ID is required"); scrollToError("field-serviceId"); return; }
    if (!/^[a-z0-9-]+$/.test(serviceId)) { setErrorField("serviceId"); setErrorMessage("⚠️ Service ID must be lowercase slug"); scrollToError("field-serviceId"); return; }
    if (!description?.trim()) { setErrorField("description"); setErrorMessage("⚠️ Description is required"); scrollToError("field-description"); return; }
    
    // Repo Validations
    if (!namespace.trim() || !repoName.trim()) { setErrorField("repo"); setErrorMessage("⚠️ Repo required"); return; }
    if (!hasScanned) { setErrorMessage("⚠️ Please Scan Repo"); return; }
    if (!selectedBranch || !selectedCommit) { setErrorMessage("⚠️ Select Branch/Commit"); return; }
    if (!command.trim()) { setErrorField("command"); setErrorMessage("⚠️ Command required"); return; }

    const payload: ServiceFormData = {
      id: serviceId,
      name,
      description,
      
      request_timeout: requestTimeout,
      idle_timeout: idleTimeout,
      max_concurrency: maxConcurrency,
      
      namespace,
      repo_name: repoName,
      branch: selectedBranch,
      commit_sha: selectedCommit,
      entry_command: command,
      
      job_task_name: jobTaskName.trim() || `[Service] ${serviceId}`,
      job_description: jobDescription, 

      gpu_count: gpuCount,
      gpu_type: gpuType,
      job_type: jobType,
      cpu_count: cpuCount || null,
      memory_demand: memoryDemand.trim() || null,
      ephemeral_storage: ephemeralStorage.trim() || null,
      runner: runner.trim() || null,
      container_image: containerImage.trim() || null,
      system_entry_command: systemEntryCommand.trim() || null,
    };
    
    try {
      await client("/api/services", { 
          method: "POST",
          json: payload 
      });
      onSuccess(); 
    } catch (e: any) {
      setErrorMessage(e.message || "Save Failed");
      const id = isOriginalId ? "btn-update" : "btn-create";
      const btn = document.getElementById(id);
      if (btn) {
         btn.classList.add("animate-shake");
         setTimeout(() => btn.classList.remove("animate-shake"), 500);
      }
    }
  };

  const isOriginalId = initialData && initialData.id === serviceId;

  const inputClass = (isError: boolean) => `
    w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
    ${isError ? 'animate-shake border-red-500' : 'border-zinc-800'}
  `;

  return (
    <div className="flex flex-col gap-8">
      
      {/* 1. Service Identity */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            {t("serviceForm.identity")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>

        <div className="mb-4" id="field-name">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'name' ? 'text-red-500' : 'text-zinc-500'}`}>
                {t("serviceForm.name")} <span className="text-red-500">*</span>
            </label>
            <input
                className={inputClass(errorField === 'name')}
                value={name}
                placeholder={t("serviceForm.namePlaceholder")}
                onChange={e => handleServiceNameChange(e.target.value)}
            />
        </div>

        <div className="mb-4" id="field-serviceId">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'serviceId' ? 'text-red-500' : 'text-zinc-500'}`}>
                {t("serviceForm.id")} <span className="text-red-500">*</span>
            </label>
            <input
                className={`${inputClass(errorField === 'serviceId')} font-mono`}
                value={serviceId}
                placeholder={t("serviceForm.idPlaceholder")}
                onChange={e => { setServiceId(e.target.value); clearError('serviceId'); }}
            />
            <p className="text-[10px] text-zinc-600 mt-1">{t("serviceForm.idHint")}</p>
        </div>

        <div className="mb-4" id="field-description">
          <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'description' ? 'text-red-500' : 'text-zinc-500'}`}>
            {t("serviceForm.description")} <span className="text-red-500">*</span>
          </label>
          <input
            className={inputClass(errorField === 'description')}
            value={description || ""}
            placeholder={t("serviceForm.descPlaceholder")}
            maxLength={200}
            onChange={e => { setDescription(e.target.value); clearError('description'); }}
          />
          <p className="text-[10px] text-zinc-600 mt-1">{(description || "").length}/200</p>
        </div>
      </div>

      {/* 2. Lifecycle & Traffic */}
      <div>
         <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            {t("serviceForm.lifecycle")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
         </h3>

         <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
                 <NumberStepper
                   label={t("serviceForm.idleTimeout")}
                   value={idleTimeout}
                   onChange={setIdleTimeout}
                   min={0}
                   max={10080}
                 />
                 <p className="text-[11px] text-zinc-500 mt-2">{t("serviceForm.idleTimeoutHint")}</p>
            </div>
            <div>
                <NumberStepper
                  label={t("serviceForm.reqTimeout")}
                  value={requestTimeout}
                  onChange={setRequestTimeout}
                  min={10}
                  max={3600}
                />
                 <p className="text-[11px] text-zinc-500 mt-2">{t("serviceForm.reqTimeoutHint")}</p>
            </div>
            <div>
                <NumberStepper
                  label={t("serviceForm.maxConcurrency")}
                  value={maxConcurrency}
                  onChange={setMaxConcurrency}
                  min={1}
                  max={1000}
                />
                <p className="text-[11px] text-zinc-500 mt-2">{t("serviceForm.maxConcurrencyHint")}</p>
            </div>
         </div>
      </div>

      {/* 3. Job Identity (Underlying) */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            <Layers className="w-4 h-4 text-zinc-500" />
            {t("serviceForm.underlyingJob")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>

        <div className="mb-4" id="field-jobTaskName">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'jobTaskName' ? 'text-red-500' : 'text-zinc-500'}`}>
                {t("serviceForm.jobTaskName")}
            </label>
            <input
                className={inputClass(errorField === 'jobTaskName')}
                value={jobTaskName}
                placeholder={`Default: [Service] ${serviceId || '...'}`}
                onChange={e => { setJobTaskName(e.target.value); clearError('jobTaskName'); }}
            />
        </div>

        <div className="mb-4">
            <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
                {t("serviceForm.jobDescription")} <span className="text-zinc-600 normal-case ml-1">({t("common.optional")})</span>
            </label>
            <textarea
                ref={jobDescriptionRef}
                className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500/50 focus:shadow-[0_0_15px_rgba(59,130,246,0.1)] outline-none transition-all placeholder-zinc-700 resize-none overflow-hidden min-h-[42px]"
                value={jobDescription}
                placeholder={t("serviceForm.jobDescPlaceholder")}
                rows={1}
                onChange={e => setJobDescription(e.target.value)}
            />
        </div>
      </div>

      {/* 4. Code Source */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            {t("jobForm.codeSource")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>

        <div className="grid grid-cols-2 gap-4 mb-4">
          <div id="field-namespace">
             <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'namespace' ? 'text-red-500' : 'text-zinc-500'}`}>{t("jobForm.namespace")}</label>
             <input className={inputClass(errorField === 'namespace')} value={namespace} onChange={e => { setNamespace(e.target.value); clearError('namespace'); }} />
          </div>
          <div id="field-repo">
             <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'repo' ? 'text-red-500' : 'text-zinc-500'}`}>{t("jobForm.repoName")}</label>
             <input className={inputClass(errorField === 'repo')} value={repoName} onChange={e => { setRepoName(e.target.value); clearError('repo'); }} />
          </div>
        </div>

        <button onClick={fetchBranches} disabled={loading} className="w-full bg-zinc-900 hover:bg-zinc-800 text-zinc-300 py-2.5 rounded-lg text-sm font-medium transition-all active:scale-[0.98] disabled:opacity-50 mb-6 border border-zinc-800 flex justify-center items-center gap-2">
            {loading ? <><span className="w-4 h-4 border-2 border-zinc-500 border-t-white rounded-full animate-spin"></span>{t("jobForm.scanning")}</> : t("jobForm.scanRepo")}
        </button>

        <div className="grid grid-cols-1 gap-0">
          <SearchableSelect
            label={t("jobForm.branch")}
            disabled={!hasScanned}
            value={selectedBranch}
            onChange={(v) => { setSelectedBranch(v); clearError('branch'); }}
            options={branches.map(b => ({ label: b.name, value: b.name }))}
            hasError={errorField === 'branch'}
            placeholder="Select branch..."
            className="mb-4"
          />
          <SearchableSelect
            label={t("jobForm.commit")}
            disabled={!hasScanned}
            value={selectedCommit}
            onChange={(v) => { setSelectedCommit(v); clearError('commit'); }}
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
            placeholder="Select commit..."
            className="mb-4"
          />
        </div>
      </div>

      {/* 5. Resources */}
      <div>
         <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            {t("jobForm.computeResources")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
         </h3>

         <div className="flex flex-col gap-4">
            <SearchableSelect
                label={t("jobForm.priority")}
                value={jobType}
                onChange={setJobType}
                options={JOB_TYPES}
                placeholder="Select Priority..."
            />
            <SearchableSelect
                label={t("jobForm.gpuAccelerator")}
                value={gpuType}
                onChange={handleGpuTypeChange}
                options={GPU_TYPES}
                placeholder="Select GPU model..."
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

         {/* Advanced (Collapsed) */}
         <div className="pt-2">
          <button type="button" onClick={() => setShowAdvanced(!showAdvanced)} className="flex items-center gap-2 text-sm font-medium text-zinc-400 hover:text-zinc-200 transition-colors select-none group">
            <div className="text-zinc-600 group-hover:text-zinc-300 transition-colors">
                {showAdvanced ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </div>
            <span>{t("common.advanced")}</span>
          </button>

          {showAdvanced && (
            <div className="mt-3 pl-1 grid grid-cols-1 sm:grid-cols-2 gap-4 animate-in slide-in-from-top-1 duration-200">
                <div>
                    <NumberStepper
                      label={t("jobForm.cpuCores")}
                      value={cpuCount}
                      onChange={setCpuCount}
                      min={0}
                      max={MAX_CPU_COUNT}
                    />
                    <p className="text-[11px] text-zinc-500 mt-1.5 ml-0.5">{t("jobForm.cpuCoresHint")}</p>
                </div>
                <div>
                      <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">{t("jobForm.memory")}</label>
                      <input
                        className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700"
                        value={memoryDemand}
                        onChange={e => setMemoryDemand(e.target.value)}
                        placeholder={t("jobForm.memoryDefault", { value: DEFAULT_MEMORY })}
                      />
                </div>
                <div>
                      <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">{t("jobForm.ephemeralStorage")}</label>
                      <input
                        className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700"
                        value={ephemeralStorage}
                        onChange={e => setEphemeralStorage(e.target.value)}
                        placeholder={t("jobForm.ephemeralStorageDefault", { value: DEFAULT_EPHEMERAL_STORAGE })}
                      />
                </div>
                <div className="sm:col-span-2">
                      <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">{t("jobForm.runAsUser")}</label>
                      <input
                        className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm font-mono focus:border-blue-500 outline-none transition-all placeholder-zinc-700"
                        value={runner}
                        onChange={e => setRunner(e.target.value)}
                        placeholder={t("jobForm.runAsUserDefault", { value: DEFAULT_RUNNER })}
                      />
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

      {/* 6. Execution */}
      <div id="field-command">
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            {t("jobForm.execution")}
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'command' ? 'text-red-500' : 'text-zinc-500'}`}>{t("jobForm.entryCommand")}</label>
        <div className={`bg-zinc-950 border rounded-lg overflow-hidden focus-within:border-green-500/50 focus-within:shadow-[0_0_15px_rgba(34,197,94,0.1)] transition-all ${errorField === 'command' ? 'border-red-500 animate-shake' : 'border-zinc-800'}`}>
            <pre className="px-3 pt-3 text-sm font-mono leading-relaxed select-text">
              <ServiceImplicitExport showDollarSign />
            </pre>
            <div className="relative">
                <span className="absolute left-3 top-3 text-zinc-600 select-none font-mono text-sm">$</span>
                <textarea
                    ref={commandRef}
                    className="w-full bg-transparent px-3 pl-7 py-3 text-green-400 font-mono text-sm focus:outline-none min-h-[100px] leading-relaxed placeholder-zinc-700 resize-none overflow-hidden"
                    value={command}
                    onChange={e => { setCommand(e.target.value); clearError('command'); }}
                    placeholder="python server.py --port $MAGNUS_PORT"
                    spellCheck={false}
                />
            </div>
        </div>
      </div>

      {/* Footer */}
      <div ref={actionRef} className="mt-2 pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4">
        {errorMessage ? (
             <span className="text-red-500 text-xs font-bold animate-pulse text-center sm:text-left">{errorMessage}</span>
        ) : (
            <span className="text-zinc-500 text-xs text-center sm:text-left hidden sm:block">{t("serviceForm.persistentDriver")}</span>
        )}
        <div className="flex gap-3 w-full sm:w-auto">
            <button
                onClick={onCancel}
                className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
            >
                {t("common.cancel")}
            </button>
            <button
                id={isOriginalId ? "btn-update" : "btn-create"}
                onClick={handleSave}
                className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2"
            >
                {isOriginalId ? t("serviceForm.updateService") : (initialData ? t("serviceForm.cloneServiceBtn") : t("serviceForm.createService"))}
            </button>
        </div>
      </div>
    </div>
  );
});

export default ServiceForm;
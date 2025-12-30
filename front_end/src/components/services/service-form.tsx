// front_end/src/components/services/service-form.tsx
"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { ChevronDown, ChevronRight, Info, Layers } from "lucide-react";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { NumberStepper } from "@/components/ui/number-stepper";
import { client } from "@/lib/api"; 
import { Service } from "@/types/service";

const MAX_GPU_COUNT = 3;

const GPU_TYPES = [
  { label: "NVIDIA GeForce RTX 5090", value: "rtx5090", meta: "32GB • Blackwell" },
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
  runner?: string | null;
}

interface ServiceFormProps {
  initialData?: ServiceFormData | Service | null; 
  onCancel: () => void;
  onSuccess: () => void;
}

export default function ServiceForm({ initialData, onCancel, onSuccess }: ServiceFormProps) {
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
  const [namespace, setNamespace] = useState(data?.namespace || "PKU-Plasma");
  const [repoName, setRepoName] = useState(data?.repo_name || "");
  
  const [branches, setBranches] = useState<Branch[]>([]);
  const [commits, setCommits] = useState<Commit[]>([]);
  
  const [selectedBranch, setSelectedBranch] = useState(data?.branch || "");
  const [selectedCommit, setSelectedCommit] = useState(data?.commit_sha || "");
  const [command, setCommand] = useState(data?.entry_command || "");
  
  // === Resources ===
  const [gpuCount, setGpuCount] = useState(data?.gpu_count ?? 1);
  const [gpuType, setGpuType] = useState(data?.gpu_type || "rtx5090"); 
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
  const [runner, setRunner] = useState<string>(data?.runner || "");

  // Refs for auto-resize textareas
  const jobDescriptionRef = useRef<HTMLTextAreaElement>(null);
  const commandRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize for Job Description (Multi-line allowed)
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

  // Sync Service Name -> Job Task Name (Convenience)
  const handleServiceNameChange = (val: string) => {
    setName(val);
    clearError('name');
    if (!initialData && !jobTaskName) {
        setJobTaskName(val);
    }
  };

  const handleGpuTypeChange = (val: string) => {
    setGpuType(val);
    if (val === 'cpu') { setGpuCount(0); } else { if (gpuCount === 0) setGpuCount(1); }
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
    if (!serviceId.trim()) { setErrorField("serviceId"); setErrorMessage("⚠️ ID is required"); scrollToError("field-serviceId"); return; }
    if (!/^[a-z0-9-]+$/.test(serviceId)) { setErrorField("serviceId"); setErrorMessage("⚠️ ID must be lowercase slug"); scrollToError("field-serviceId"); return; }
    if (!name.trim()) { setErrorField("name"); setErrorMessage("⚠️ Name is required"); scrollToError("field-name"); return; }
    
    // Job Metadata Validations
    if (!jobTaskName.trim()) { setErrorField("jobTaskName"); setErrorMessage("⚠️ Job Task Name is required"); scrollToError("field-jobTaskName"); return; }
    
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
      
      job_task_name: jobTaskName,
      job_description: jobDescription, // Allow empty if user wants

      gpu_count: gpuCount,
      gpu_type: gpuType,
      job_type: jobType,
      cpu_count: cpuCount || null,
      memory_demand: memoryDemand.trim() || null,
      runner: runner.trim() || null,
    };
    
    try {
      await client("/api/services", { json: payload });
      onSuccess(); 
    } catch (e: any) {
      setErrorMessage(e.message || "Save Failed");
      alert(`❌ Error: ${e.message}`);
    }
  };

  const isEdit = !!initialData;

  const inputClass = (isError: boolean) => `
    w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
    ${isError ? 'animate-shake border-red-500' : 'border-zinc-800'}
  `;

  return (
    <div className="flex flex-col gap-8">
      
      {/* 1. Service Identity */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Service Identity
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        
        <div className="mb-4" id="field-serviceId">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'serviceId' ? 'text-red-500' : 'text-zinc-500'}`}>
                Service ID (Unique Slug) <span className="text-red-500">*</span>
            </label>
            <input 
                className={`${inputClass(errorField === 'serviceId')} font-mono`}
                value={serviceId} 
                disabled={isEdit} 
                placeholder="e.g. jupyter-lab-01"
                onChange={e => { setServiceId(e.target.value); clearError('serviceId'); }} 
            />
        </div>

        <div className="mb-4" id="field-name">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'name' ? 'text-red-500' : 'text-zinc-500'}`}>
                Display Name <span className="text-red-500">*</span>
            </label>
            <input 
                className={inputClass(errorField === 'name')}
                value={name} 
                placeholder="e.g. My Interactive Environment"
                onChange={e => handleServiceNameChange(e.target.value)} 
            />
        </div>

        <div className="mb-4">
          <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
            Description
          </label>
          {/* 单行 Input，严禁换行 */}
          <input 
            className={inputClass(false)}
            value={description || ""} 
            placeholder="Service description (Single line)"
            onChange={e => setDescription(e.target.value)} 
          />
        </div>
      </div>

      {/* 2. Lifecycle & Traffic */}
      <div>
         <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Lifecycle & Traffic
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
         </h3>
         
         {/* 这里的 Grid 保持 3 列是因为它们都是小控件，且逻辑相关 */}
         <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
                 <NumberStepper 
                   label="Idle Timeout (Mins)"
                   value={idleTimeout}
                   onChange={setIdleTimeout}
                   min={0}
                   max={1440}
                 />
                 <p className="text-[11px] text-zinc-500 mt-2">Auto-stop. 0 to disable.</p>
            </div>
            <div>
                <NumberStepper 
                  label="Req Timeout (Secs)"
                  value={requestTimeout}
                  onChange={setRequestTimeout}
                  min={10}
                  max={300}
                />
                 <p className="text-[11px] text-zinc-500 mt-2">Cold-start budget.</p>
            </div>
            <div>
                <NumberStepper 
                  label="Max Concurrency"
                  value={maxConcurrency}
                  onChange={setMaxConcurrency}
                  min={1}
                  max={1000}
                />
                <p className="text-[11px] text-zinc-500 mt-2">Simultaneous requests.</p>
            </div>
         </div>
      </div>

      {/* 3. Job Identity (Underlying) - Aligned with Job Form */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            <Layers className="w-4 h-4 text-zinc-500" />
            Underlying Job Config
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        
        <div className="mb-4" id="field-jobTaskName">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'jobTaskName' ? 'text-red-500' : 'text-zinc-500'}`}>
                Job Task Name <span className="text-red-500">*</span>
            </label>
            <input 
                className={inputClass(errorField === 'jobTaskName')}
                value={jobTaskName} 
                placeholder="e.g. magnus-jupyter-worker"
                onChange={e => { setJobTaskName(e.target.value); clearError('jobTaskName'); }} 
            />
        </div>

        <div className="mb-4">
            <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
                Job Description <span className="text-zinc-600 normal-case ml-1">(Optional)</span>
            </label>
            {/* 多行 Textarea，允许换行，与 Job Form 一致 */}
            <textarea 
                ref={jobDescriptionRef}
                className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500/50 focus:shadow-[0_0_15px_rgba(59,130,246,0.1)] outline-none transition-all placeholder-zinc-700 resize-none overflow-hidden min-h-[42px]"
                value={jobDescription} 
                placeholder="Worker process description..."
                rows={1}
                onChange={e => setJobDescription(e.target.value)} 
            />
        </div>
      </div>

      {/* 4. Code Source - Aligned with Job Form */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Code Source
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div id="field-namespace">
             <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'namespace' ? 'text-red-500' : 'text-zinc-500'}`}>Namespace</label>
             <input className={inputClass(errorField === 'namespace')} value={namespace} onChange={e => { setNamespace(e.target.value); clearError('namespace'); }} />
          </div>
          <div id="field-repo">
             <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'repo' ? 'text-red-500' : 'text-zinc-500'}`}>Repo Name</label>
             <input className={inputClass(errorField === 'repo')} value={repoName} onChange={e => { setRepoName(e.target.value); clearError('repo'); }} />
          </div>
        </div>

        <button onClick={fetchBranches} disabled={loading} className="w-full bg-zinc-900 hover:bg-zinc-800 text-zinc-300 py-2.5 rounded-lg text-sm font-medium transition-all active:scale-[0.98] disabled:opacity-50 mb-6 border border-zinc-800 flex justify-center items-center gap-2">
            {loading ? <><span className="w-4 h-4 border-2 border-zinc-500 border-t-white rounded-full animate-spin"></span>Scanning...</> : "Scan Repository"}
        </button>

        <div className="grid grid-cols-1 gap-0">
          <SearchableSelect 
            label="Branch" 
            disabled={!hasScanned} 
            value={selectedBranch} 
            onChange={(v) => { setSelectedBranch(v); clearError('branch'); }} 
            options={branches.map(b => ({ label: b.name, value: b.name }))} 
            hasError={errorField === 'branch'}
            placeholder="Select branch..."
            className="mb-4"
          />
          <SearchableSelect 
            label="Commit" 
            disabled={!hasScanned} 
            value={selectedCommit} 
            onChange={(v) => { setSelectedCommit(v); clearError('commit'); }} 
            options={commits.map(c => ({ label: c.message, value: c.sha, meta: `${c.sha.substring(0, 7)} • ${c.author}` }))}
            hasError={errorField === 'commit'}
            placeholder="Select commit..."
            className="mb-4"
          />
        </div>
      </div>

      {/* 5. Resources - Aligned with Job Form */}
      <div>
         <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Compute Resources
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
         </h3>

         <div className="flex flex-col gap-4">
            <SearchableSelect 
                label="Job Priority" 
                value={jobType} 
                onChange={setJobType} 
                options={JOB_TYPES} 
                placeholder="Select Priority..."
            />
            <SearchableSelect 
                label="GPU Accelerator" 
                value={gpuType} 
                onChange={handleGpuTypeChange} 
                options={GPU_TYPES} 
                placeholder="Select GPU model..."
            />
            <NumberStepper 
                label="GPU Count" 
                value={gpuCount} 
                onChange={setGpuCount} 
                min={0} 
                max={MAX_GPU_COUNT} 
                disabled={gpuType === 'cpu'} 
            />
         </div>
         
         {/* Advanced (Collapsed) */}
         <div className="pt-2">
          <button type="button" onClick={() => setShowAdvanced(!showAdvanced)} className="flex items-center gap-2 text-sm font-medium text-zinc-400 hover:text-zinc-200 transition-colors select-none group">
            <div className="text-zinc-600 group-hover:text-zinc-300 transition-colors">
                {showAdvanced ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </div>
            <span>Advanced</span>
          </button>
          
          {showAdvanced && (
            <div className="mt-3 pl-1 grid grid-cols-1 sm:grid-cols-2 gap-4 animate-in slide-in-from-top-1 duration-200">
                <div>
                    <NumberStepper label="CPU Cores" value={cpuCount} onChange={setCpuCount} min={0} max={64} />
                    <p className="text-[11px] text-zinc-500 mt-1.5 ml-0.5">Set to <span className="text-zinc-400 font-mono">0</span> to use default.</p>
                </div>
                <div>
                     <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">Memory</label>
                     <input className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700" value={memoryDemand} onChange={e => setMemoryDemand(e.target.value)} placeholder="Default: 1600M" />
                </div>
                <div className="sm:col-span-2">
                     <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">Run As User</label>
                     <input className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm font-mono focus:border-blue-500 outline-none transition-all placeholder-zinc-700" value={runner} onChange={e => setRunner(e.target.value)} placeholder="Default: magnus" />
                </div>
            </div>
          )}
        </div>
      </div>

      {/* 6. Execution - Aligned with Job Form */}
      <div id="field-command">
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Execution
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'command' ? 'text-red-500' : 'text-zinc-500'}`}>Entry Command</label>
        <div className="relative group">
            <span className="absolute left-3 top-3 text-zinc-600 select-none font-mono text-sm">$</span>
            <textarea 
                ref={commandRef} 
                className={`w-full bg-zinc-950 border px-3 pl-7 py-3 rounded-lg text-green-400 font-mono text-sm focus:border-green-500/50 focus:shadow-[0_0_15px_rgba(34,197,94,0.1)] outline-none shadow-inner min-h-[100px] leading-relaxed placeholder-zinc-800 resize-none overflow-hidden
                    ${errorField === 'command' ? 'animate-shake border-red-500' : 'border-zinc-800'}`}
                value={command} 
                onChange={e => { setCommand(e.target.value); clearError('command'); }} 
                placeholder="python server.py" 
                spellCheck={false}
            />
        </div>
      </div>

      {/* Footer */}
      <div className="mt-2 pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4">
        {errorMessage ? (
             <span className="text-red-500 text-xs font-bold animate-pulse text-center sm:text-left">{errorMessage}</span>
        ) : (
            <span className="text-zinc-500 text-xs text-center sm:text-left hidden sm:block">Persistent service driver.</span>
        )}
        <div className="flex gap-3 w-full sm:w-auto">
            <button 
                onClick={onCancel} 
                className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
            >
                Cancel
            </button>
            <button 
                onClick={handleSave} 
                className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2"
            >
                {isEdit ? "📡 Update Service" : "📡 Create Service"}
            </button>
        </div>
      </div>
    </div>
  );
}
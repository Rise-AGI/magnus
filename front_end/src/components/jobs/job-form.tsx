"use client";

import { useState, useEffect, useRef } from "react";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { NumberStepper } from "@/components/ui/number-stepper";
import { client } from "@/lib/api"; 

const MAX_GPU_COUNT = 8;

const GPU_TYPES = [
  { label: "NVIDIA GeForce RTX 5090", value: "RTX_5090", meta: "32GB • Blackwell" },
  { label: "CPU Only", value: "CPU", meta: "Host Memory" },
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
}

interface JobFormProps {
  mode: "create" | "clone";
  initialData?: JobFormData | null; 
  onCancel: () => void;
  onSuccess: () => void;
}

export default function JobForm({ mode, initialData, onCancel, onSuccess }: JobFormProps) {
  // State Initialization
  const [taskName, setTaskName] = useState(initialData?.taskName || "");
  const [description, setDescription] = useState(initialData?.description || "");
  const [namespace, setNamespace] = useState(initialData?.namespace || "PKU-Plasma");
  const [repoName, setRepoName] = useState(initialData?.repoName || "");
  
  const [branches, setBranches] = useState<Branch[]>([]);
  const [commits, setCommits] = useState<Commit[]>([]);
  
  const [selectedBranch, setSelectedBranch] = useState(initialData?.branch || "");
  const [selectedCommit, setSelectedCommit] = useState(initialData?.commit_sha || "");
  const [command, setCommand] = useState(initialData?.entry_command || "");
  
  // Logic: 如果初始数据是 0 GPU，强制类型为 CPU，防止前端状态不一致
  const [gpuCount, setGpuCount] = useState(initialData?.gpu_count ?? 1);
  const [gpuType, setGpuType] = useState(
    initialData?.gpu_type || (initialData?.gpu_count === 0 ? "CPU" : "")
  ); 
  
  const [jobType, setJobType] = useState(initialData?.job_type || "A2");

  const [loading, setLoading] = useState(false);
  const [hasScanned, setHasScanned] = useState(false);
  
  const [errorField, setErrorField] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Auto-resize textarea
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [command]);

  // Init for Clone Mode
  useEffect(() => {
    if (mode === 'clone' && initialData) {
        setHasScanned(true); 
        fetchBranches();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); 

  // --- Handlers ---

  // 核心修复：手动处理 GPU 类型切换逻辑，替代 useEffect
  const handleGpuTypeChange = (val: string) => {
    setGpuType(val);
    if (val === 'CPU') {
        setGpuCount(0);
    } else {
        // 如果从 CPU (0) 切回 GPU，且当前数量为 0，则自动设为 1
        if (gpuCount === 0) setGpuCount(1);
    }
  };

  const clearError = (field: string) => {
    if (errorField === field) { setErrorField(null); setErrorMessage(null); }
  };

  const scrollToError = (id: string) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const fetchBranches = async () => {
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
  };

  // Fetch commits when branch changes
  useEffect(() => {
    if (!selectedBranch || !hasScanned) return;

    const fetchCommits = async () => {
      try {
        const data = await client(`/api/github/${namespace}/${repoName}/commits?branch=${selectedBranch}`);
        setCommits(data);
        if (mode === 'create' && data.length > 0) {
            setSelectedCommit(data[0].sha);
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
    };
    
    try {
      await client("/api/jobs/submit", { json: payload });
      onSuccess(); 
    } catch (e: any) {
      console.error(e);
      setErrorMessage(e.message || "Submit Failed");
      alert(`❌ Error: ${e.message}`);
    }
  };

  return (
    <div className="flex flex-col gap-8">

      {/* Task Info */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Task Information
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        
        <div className="mb-4" id="field-taskName">
          <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'taskName' ? 'text-red-500' : 'text-zinc-500'}`}>
            Task Name <span className="text-red-500">*</span>
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
            Description <span className="text-zinc-600 normal-case ml-1">(Optional)</span>
          </label>
          <input 
            className="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700"
            value={description} 
            placeholder="Brief description of this experiment..."
            onChange={e => setDescription(e.target.value)} 
          />
        </div>
      </div>
      
      {/* Code Source */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Code Source
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div id="field-namespace">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'namespace' ? 'text-red-500' : 'text-zinc-500'}`}>Namespace</label>
            <input 
              className={`w-full bg-zinc-950 border px-3 py-2.5 rounded-lg text-white text-sm focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                ${errorField === 'namespace' ? 'animate-shake border-red-500' : 'border-zinc-800'}`} 
              value={namespace} 
              placeholder="e.g. PKU-Plasma"
              onChange={e => { setNamespace(e.target.value); clearError('namespace'); }} 
            />
          </div>
          <div id="field-repo">
            <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'repo' ? 'text-red-500' : 'text-zinc-500'}`}>Repo Name</label>
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
                 Scanning...
                </>
            ) : "Scan Repository"}
        </button>

        <div className="grid grid-cols-1 gap-0">
          <SearchableSelect 
            id="field-branch" label="Branch" disabled={!hasScanned} placeholder="Select branch..." className="mb-4"
            value={selectedBranch} onChange={(v) => { setSelectedBranch(v); clearError('branch'); }} 
            options={branches.map(b => ({ label: b.name, value: b.name }))} 
            hasError={errorField === 'branch'}
          />
          <SearchableSelect 
            id="field-commit" label="Commit" disabled={!hasScanned} placeholder="Select commit..." className="mb-4"
            value={selectedCommit} onChange={(v) => { setSelectedCommit(v); clearError('commit'); }} 
            options={commits.map(c => ({ label: c.message, value: c.sha, meta: `${c.sha.substring(0, 7)} • ${c.author}` }))} 
            hasError={errorField === 'commit'}
          />
        </div>
      </div>

      {/* Job Scheduling */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Job Scheduling
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        
        <SearchableSelect 
            label="Job Priority" 
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
            Compute Resources
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        
        <div className="grid grid-cols-1 gap-4">
            <SearchableSelect 
                label="GPU Accelerator" 
                value={gpuType} 
                onChange={handleGpuTypeChange} 
                options={GPU_TYPES}
                placeholder="Select GPU model..."
                className="mb-4"
            />
            <NumberStepper 
                label="GPU Count"
                value={gpuCount} 
                onChange={setGpuCount} 
                min={0}
                max={MAX_GPU_COUNT}
                disabled={gpuType === 'CPU'} 
            />
        </div>
      </div>

      {/* Execution */}
      <div id="field-command">
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Execution
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${errorField === 'command' ? 'text-red-500' : 'text-zinc-500'}`}>Entry Command</label>
        <div className="relative group">
            <span className="absolute left-3 top-3 text-zinc-600 select-none font-mono text-sm">$</span>
            <textarea 
                ref={textareaRef}
                className={`w-full bg-zinc-950 border px-3 pl-7 py-3 rounded-lg text-green-400 font-mono text-sm focus:border-green-500/50 outline-none shadow-inner min-h-[100px] leading-relaxed placeholder-zinc-800 resize-none overflow-hidden
                ${errorField === 'command' ? 'animate-shake border-red-500' : 'border-zinc-800'}`}
                value={command} 
                placeholder="python train.py ..."
                onChange={e => { setCommand(e.target.value); clearError('command'); }}
                spellCheck={false}
            />
        </div>
      </div>

      {/* Action Bar */}
      <div className="mt-4 pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4">
        {errorMessage ? (
             <span className="text-red-500 text-xs font-bold animate-pulse text-center sm:text-left">{errorMessage}</span>
        ) : (
            <span className="text-zinc-500 text-xs text-center sm:text-left hidden sm:block">Waiting for launch</span>
        )}
        
        <div className="flex gap-3 w-full sm:w-auto">
            <button 
                onClick={onCancel} 
                className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
            >
                Cancel
            </button>
            <button 
                onClick={handleLaunch}
                className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2"
            >
                {mode === 'create' ? "🚀 Launch Job" : "🔁 Re-Launch"}
            </button>
        </div>
      </div>

    </div>
  );
}
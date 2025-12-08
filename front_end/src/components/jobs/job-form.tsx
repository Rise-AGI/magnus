// front_end/src/components/jobs/job-form.tsx
"use client";


import { useState, useEffect, useRef } from "react";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { NumberStepper } from "@/components/ui/number-stepper";
import { API_BASE } from "@/lib/config";


// 这些我以后想从环境中读，不过暂时先写死吧
const MAX_GPU_COUNT = 2;
const GPU_TYPES = [
  { label: "NVIDIA GeForce RTX 5090", value: "RTX_5090", meta: "32GB • Blackwell" },
  { label: "CPU Only", value: "CPU", meta: "Host Memory" },
];


// --- 类型定义 ---
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
}

interface JobFormProps {
  mode: "create" | "clone";
  initialData?: JobFormData | null; 
  onCancel: () => void;
  onSuccess: () => void;
}

export default function JobForm({ mode, initialData, onCancel, onSuccess }: JobFormProps) {
  // --- State Initialization ---
  const [taskName, setTaskName] = useState(initialData?.taskName || "");
  const [description, setDescription] = useState(initialData?.description || "");

  const [namespace, setNamespace] = useState(initialData?.namespace || ""); 
  const [repoName, setRepoName] = useState(initialData?.repoName || "");
  
  const [branches, setBranches] = useState<Branch[]>([]);
  const [commits, setCommits] = useState<Commit[]>([]);
  
  const [selectedBranch, setSelectedBranch] = useState(initialData?.branch || "");
  const [selectedCommit, setSelectedCommit] = useState(initialData?.commit_sha || "");
  const [command, setCommand] = useState(initialData?.entry_command || "");
  
  const [gpuCount, setGpuCount] = useState(initialData?.gpu_count || 1);
  const [gpuType, setGpuType] = useState(initialData?.gpu_type || ""); 

  const [loading, setLoading] = useState(false);
  const [hasScanned, setHasScanned] = useState(false);
  
  const [errorField, setErrorField] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // --- Logic: GPU/CPU 联动逻辑 ---
  useEffect(() => {
    if (gpuType === 'CPU') {
        setGpuCount(0); // CPU 模式强制为 0
    } else {
        // 切回 GPU 时，如果数量为 0 则恢复为 1
        if (gpuCount === 0) setGpuCount(1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gpuType]); 

  // --- Auto-Height Textarea ---
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [command]);

  // --- Clone Initialization ---
  useEffect(() => {
    if (mode === 'clone' && initialData) {
        setHasScanned(true); 
        fetchBranches();
    }
  }, []);

  const clearError = (field: string) => {
    if (errorField === field) { setErrorField(null); setErrorMessage(null); }
  };

  // --- API Calls ---
  const fetchBranches = async () => {
    if (!namespace.trim()) { setErrorField("namespace"); setErrorMessage("⚠️ Namespace is required"); return; }
    if (!repoName.trim()) { setErrorField("repo"); setErrorMessage("⚠️ Repo Name is required"); return; }
    
    setLoading(true);
    if (mode === 'create') {
        setBranches([]); setCommits([]); setSelectedBranch(""); setSelectedCommit(""); 
    }
    
    setErrorField(null); setErrorMessage(null);
    try {
      const res = await fetch(`${API_BASE}/api/github/${namespace}/${repoName}/branches`);
      if (!res.ok) throw new Error("Failed");
      const data = await res.json();
      setBranches(data);
      
      if (mode === 'create' && data.length > 0) setSelectedBranch(data[0].name);
      
      setHasScanned(true);
    } catch (e) {
      alert(`❌ Backend Offline: ${API_BASE}`); 
    } finally { setLoading(false); }
  };

  useEffect(() => {
    if (!selectedBranch || !hasScanned) return;
    const fetchCommits = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/github/${namespace}/${repoName}/commits?branch=${selectedBranch}`);
        const data = await res.json();
        setCommits(data);
        if (mode === 'create' && data.length > 0) setSelectedCommit(data[0].sha);
      } catch (e) { console.error(e); }
    };
    fetchCommits();
  }, [selectedBranch, hasScanned]);

  const scrollToError = (id: string) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

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
    };
    
    try {
      const res = await fetch(`${API_BASE}/api/jobs/submit`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      });
      const result = await res.json();
      alert(`✅ ${result.msg}`);
      onSuccess(); // 关闭抽屉
    } catch (e) { alert("❌ Submit Failed"); }
  };

  return (
    <div className="flex flex-col gap-8">

        {/* 🆕 Section: Task Info (新插入的区块) */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Task Information
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        
        {/* Task Name Input */}
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

        {/* Description Input (Optional) */}
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
      
      {/* ... 下面接着原来的 "1. Code Source" 区块 ... */}
      
      {/* 1. Code Source */}
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
            className="w-full bg-zinc-900 hover:bg-zinc-800 text-zinc-300 py-2.5 rounded-lg text-sm font-medium transition-all active:scale-[0.98] disabled:opacity-50 mb-6 border border-zinc-800"
        >
            {loading ? "Scanning Repository..." : "Scan Repository"}
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

      {/* 2. Compute Resources */}
      <div>
        <h3 className="text-zinc-200 text-sm font-semibold mb-4 flex items-center gap-2">
            Compute Resources
            <div className="h-px bg-zinc-800 flex-grow ml-2"></div>
        </h3>
        <SearchableSelect 
            label="GPU Accelerator" value={gpuType} onChange={setGpuType} 
            options={GPU_TYPES}
            placeholder="Select GPU model..."
            className="mb-4"
        />
        {/* 👇 使用通用的 NumberStepper，并传入业务限制 */}
        <NumberStepper 
            label="GPU Count"
            value={gpuCount} 
            onChange={setGpuCount} 
            min={0}
            max={MAX_GPU_COUNT} // 传入双卡限制
            disabled={gpuType === 'CPU'} 
        />
      </div>

      {/* 3. Execution */}
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
                className={`w-full bg-zinc-950 border px-3 pl-7 py-3 rounded-lg text-green-400 font-mono text-sm focus:border-green-500/50 outline-none shadow-inner min-h-[100px] leading-relaxed placeholder-zinc-800
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
                className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all"
            >
                {mode === 'create' ? "🚀 Launch Job" : "🔁 Re-Launch"}
            </button>
        </div>
      </div>

    </div>
  );
}
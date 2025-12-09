// front_end/src/components/jobs/job-form.tsx
"use client";

import { useState, useEffect, useRef } from "react";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { NumberStepper } from "@/components/ui/number-stepper";
import { client } from "@/lib/api"; // 👈 核心改动：引入统一 API 客户端

// 配置常量 (未来可移至全局 Config)
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

  const [namespace, setNamespace] = useState(initialData?.namespace || "PKU-Plasma"); // 默认值优化
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

  // --- Logic: GPU/CPU 联动 ---
  useEffect(() => {
    if (gpuType === 'CPU') {
        setGpuCount(0);
    } else {
        if (gpuCount === 0) setGpuCount(1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gpuType]); 

  // --- Logic: Auto-Height Textarea ---
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [command]);

  // --- Logic: Init for Clone Mode ---
  useEffect(() => {
    if (mode === 'clone' && initialData) {
        setHasScanned(true); 
        // 自动触发一次 fetch，确保下拉框有数据
        fetchBranches();
    }
  }, []); // eslint-disable-line

  const clearError = (field: string) => {
    if (errorField === field) { setErrorField(null); setErrorMessage(null); }
  };

  const scrollToError = (id: string) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  // --- API 1: Fetch Branches ---
  const fetchBranches = async () => {
    if (!namespace.trim()) { setErrorField("namespace"); setErrorMessage("⚠️ Namespace is required"); return; }
    if (!repoName.trim()) { setErrorField("repo"); setErrorMessage("⚠️ Repo Name is required"); return; }
    
    setLoading(true);
    setErrorField(null); setErrorMessage(null);

    // 如果是新建模式，扫描时清空旧选择
    if (mode === 'create') {
        setBranches([]); setCommits([]); setSelectedBranch(""); setSelectedCommit(""); 
    }
    
    try {
      // ✅ 使用 client，自动处理 Auth，无需手动 headers
      const data = await client(`/api/github/${namespace}/${repoName}/branches`);
      
      setBranches(data);
      setHasScanned(true);

      // 默认选中第一个分支
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

  // --- API 2: Fetch Commits (当分支改变时) ---
  useEffect(() => {
    if (!selectedBranch || !hasScanned) return;

    const fetchCommits = async () => {
      try {
        // ✅ 使用 client
        const data = await client(`/api/github/${namespace}/${repoName}/commits?branch=${selectedBranch}`);
        setCommits(data);
        
        // 默认选中第一个 Commit
        if (mode === 'create' && data.length > 0) {
            setSelectedCommit(data[0].sha);
        }
      } catch (e) { 
        console.error("Fetch commits failed", e); 
      }
    };
    
    fetchCommits();
  }, [selectedBranch, hasScanned, namespace, repoName, mode]);


  // --- API 3: Submit Job ---
  const handleLaunch = async () => {
    setErrorField(null); setErrorMessage(null);

    // Validation
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
      // ✅ 使用 client 提交，自动带上 Token
      await client("/api/jobs", {
        json: payload 
      });
      
      // 成功回调
      onSuccess(); 
    } catch (e: any) {
      console.error(e);
      setErrorMessage(e.message || "Submit Failed");
      alert(`❌ Error: ${e.message}`);
    }
  };

  return (
    <div className="flex flex-col gap-8">

      {/* --- Section 1: Task Info --- */}
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
      
      {/* --- Section 2: Code Source --- */}
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

      {/* --- Section 3: Compute Resources --- */}
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
        <NumberStepper 
            label="GPU Count"
            value={gpuCount} 
            onChange={setGpuCount} 
            min={0}
            max={MAX_GPU_COUNT}
            disabled={gpuType === 'CPU'} 
        />
      </div>

      {/* --- Section 4: Execution --- */}
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

      {/* --- Action Bar --- */}
      <div className="mt-4 pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4">
        {errorMessage ? (
             <span className="text-red-500 text-xs font-bold animate-pulse text-center sm:text-left">{errorMessage}</span>
        ) : (
            <span className="text-zinc-500 text-xs text-center sm:text-left hidden sm:block">Ready to launch</span>
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
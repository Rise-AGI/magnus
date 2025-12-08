"use client";

import { useState, useEffect } from "react";
import { Plus, Search, RefreshCw, Box, Rocket, Copy, Check, Loader2 } from "lucide-react";
import JobForm, { JobFormData } from "@/components/jobs/job-form";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { API_BASE } from "@/lib/config";

interface Job {
  id: string; 
  task_name: string;
  description?: string;
  user_id: string;
  status: string;
  namespace: string;
  repo_name: string;
  branch: string;
  commit_sha: string;
  gpu_count: number;
  gpu_type: string;
  entry_command: string;
  created_at: string;
}

function CopyableId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(id);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button 
      onClick={handleCopy} 
      className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-blue-400 transition-colors group/id"
      title="Click to copy full ID"
    >
      <span className="font-mono">{id}</span>
      {copied ? (
        <Check className="w-3 h-3 text-green-500" />
      ) : (
        <Copy className="w-3 h-3 opacity-0 group-hover/id:opacity-100 transition-opacity" />
      )}
    </button>
  );
}

const USER_FILTER_OPTIONS = [
  { label: "All Users", value: "all" },
  { label: "My Jobs Only", value: "mine" },
];

export default function JobsPage() {
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [drawerMode, setDrawerMode] = useState<"create" | "clone">("create");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  
  const [userFilter, setUserFilter] = useState("all");
  const [cloneData, setCloneData] = useState<JobFormData | null>(null);

  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchJobs = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/jobs`);
      if (res.ok) {
        const data = await res.json();
        setJobs(data);
      } else {
        console.error("Failed to fetch jobs");
      }
    } catch (e) {
      console.error("Backend offline?", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();
  }, []);

  const handleNewJob = () => {
    setDrawerMode("create");
    setCloneData(null); 
    setSelectedJobId(null);
    setIsDrawerOpen(true);
  };

  const handleCloneJob = (job: Job) => {
    setDrawerMode("clone");
    setSelectedJobId(job.id);
    
    setCloneData({
        taskName: `${job.task_name}-copy`,
        description: job.description || "",
        namespace: job.namespace, 
        repoName: job.repo_name,
        branch: job.branch,
        commit_sha: job.commit_sha,
        entry_command: job.entry_command,
        gpu_count: job.gpu_count,
        gpu_type: job.gpu_type
    });
    
    setIsDrawerOpen(true);
  };

  // --- Date Formatter ---
  const formatBeijingTime = (isoString: string) => {
    if (!isoString) return "--";
    const date = new Date(isoString.endsWith("Z") ? isoString : `${isoString}Z`);
    return date.toLocaleString('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    }).replace(/\//g, '-'); 
  };

  return (
    <div className="relative min-h-[calc(100vh-8rem)]">
      
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Job Management</h1>
          <p className="text-zinc-500 text-sm mt-1">Manage and monitor training tasks.</p>
        </div>
        <button 
          onClick={handleNewJob} 
          className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95"
        >
          <Plus className="w-4 h-4" /> New Job
        </button>
      </div>

      <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-4 mb-6 flex items-end gap-4">
        <div className="relative flex-1 max-w-md"> 
          <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">
            Search Jobs
          </label>
          <div className="relative group">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 group-focus-within:text-blue-500 transition-colors" />
            <input 
              type="text" 
              placeholder="Search by ID or Task Name..." 
              className="w-full bg-zinc-950 border border-zinc-800 rounded-lg pl-10 pr-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-blue-500 transition-all placeholder-zinc-700 hover:border-zinc-700"
            />
          </div>
        </div>

        <div className="w-64"> 
          <SearchableSelect
             label="Filter by User" 
             value={userFilter}
             onChange={setUserFilter}
             options={USER_FILTER_OPTIONS}
             placeholder="Select user..."
             className="mb-0" 
          />
        </div>
      </div>

      <div className="border border-zinc-800 rounded-lg overflow-hidden bg-zinc-900/30 min-h-[300px]">
        {loading ? (
           <div className="flex flex-col items-center justify-center h-64 text-zinc-500 gap-3">
             <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
             <p className="text-sm">Fetching jobs from backend...</p>
           </div>
        ) : jobs.length === 0 ? (
           <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
             <Box className="w-10 h-10 mb-2 opacity-20" />
             <p>No jobs found.</p>
           </div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-900/80 text-zinc-400 border-b border-zinc-800">
              <tr>
                <th className="px-6 py-4 font-medium w-1/4">Task / Task ID</th>
                <th className="px-6 py-4 font-medium">Status</th>
                <th className="px-6 py-4 font-medium">Repo / Branch · Commit</th>
                <th className="px-6 py-4 font-medium">Resources</th>
                <th className="px-6 py-4 font-medium">Created at / Creator</th>
                <th className="px-6 py-4 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/50">
              {jobs.map((job) => (
                <tr key={job.id} className="hover:bg-zinc-800/30 transition-colors group">
                  <td className="px-6 py-4">
                    <div className="flex flex-col gap-1">
                      <span className="font-medium text-zinc-200">{job.task_name}</span>
                      <CopyableId id={job.id} />
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border 
                      ${job.status === 'Running' ? 'bg-blue-500/10 text-blue-400 border-blue-500/20' : 
                        job.status === 'Failed' ? 'bg-red-500/10 text-red-400 border-red-500/20' : 
                        job.status === 'Pending' ? 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20' :
                        'bg-green-500/10 text-green-400 border-green-500/20'}`}>
                      {job.status}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                      <div className="flex flex-col">
                          <span className="text-zinc-300 flex items-center gap-1.5">
                            <Box className="w-3.5 h-3.5 text-zinc-500"/> {job.repo_name}
                          </span>
                          <span className="text-zinc-500 text-xs font-mono mt-0.5 ml-5">
                            {job.branch} • {job.commit_sha.substring(0, 7)}
                          </span>
                      </div>
                  </td>
                  <td className="px-6 py-4 text-zinc-400">
                    {job.gpu_type === 'CPU' 
                        ? <span className="text-zinc-500">CPU Only</span>
                        : <span>{job.gpu_type.replace(/_/g, ' ')} × {job.gpu_count}</span>
                    }
                  </td>
                  <td className="px-6 py-4 text-zinc-500 whitespace-nowrap">
                    <div className="flex flex-col">
                        <span className="text-zinc-300">{formatBeijingTime(job.created_at)}</span>
                        <span className="text-xs text-zinc-600">{job.user_id}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex justify-end gap-2 opacity-60 group-hover:opacity-100 transition-opacity">
                      <button 
                          onClick={() => handleCloneJob(job)} 
                          className="p-1.5 hover:bg-zinc-700 rounded text-zinc-400 hover:text-white transition-colors" 
                          title="Clone & Rerun"
                      >
                        <RefreshCw className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {isDrawerOpen && (
        <div 
          onClick={() => setIsDrawerOpen(false)} 
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[90] transition-opacity" 
        />
      )}

      <div className={`fixed top-0 right-0 h-full w-[600px] bg-[#0A0A0C] border-l border-zinc-800 shadow-2xl z-[100] transform transition-transform duration-300 ease-in-out ${isDrawerOpen ? 'translate-x-0' : 'translate-x-full'}`}>
        <div className="h-full flex flex-col relative">
          
          <div className="px-6 py-5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900/50">
            <div>
                <h2 className="text-lg font-bold text-white flex items-center gap-2">
                    {drawerMode === 'create' ? <Rocket className="w-5 h-5 text-blue-500"/> : <RefreshCw className="w-5 h-5 text-purple-500"/>}
                    {drawerMode === 'create' ? "Submit New Job" : `Clone Job`}
                </h2>
                {drawerMode === 'clone' && <p className="text-xs text-zinc-500 mt-1">Configurations pre-filled from task #{selectedJobId?.substring(0, 8)}</p>}
            </div>
            <button onClick={() => setIsDrawerOpen(false)} className="text-zinc-500 hover:text-white transition-colors">✕</button>
          </div>

          <div className="flex-1 overflow-y-auto p-6 custom-scrollbar relative">
            <JobForm 
                key={drawerMode + (selectedJobId || "")} 
                mode={drawerMode}
                initialData={cloneData}
                onCancel={() => setIsDrawerOpen(false)}
                onSuccess={() => {
                   setIsDrawerOpen(false);
                   fetchJobs(); 
                }}
            />
          </div>
          
        </div>
      </div>

    </div>
  );
}
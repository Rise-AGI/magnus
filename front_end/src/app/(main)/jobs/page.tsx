// front_end/src/app/(main)/jobs/page.tsx
"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Plus, Search, RefreshCw, Box, Loader2, SquareX } from "lucide-react";
import { JobFormData } from "@/components/jobs/job-form";
import { JobDrawer } from "@/components/jobs/job-drawer";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { CopyableText } from "@/components/ui/copyable-text";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { client } from "@/lib/api";
import { useAuth } from "@/context/auth-context";
import { POLL_INTERVAL } from "@/lib/config";
import { useRouter } from "next/navigation";
import { Job, User } from "@/types/job";
import { formatBeijingTime } from "@/lib/utils";
import { JobPriorityBadge } from "@/components/jobs/job-priority-badge";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { UserAvatar } from "@/components/ui/user-avatar";

export default function JobsPage() {
  const router = useRouter();
  const { user: currentUser } = useAuth();

  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [drawerMode, setDrawerMode] = useState<"create" | "clone">("create");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [cloneData, setCloneData] = useState<JobFormData | null>(null);

  const [jobs, setJobs] = useState<Job[]>([]);
  const [allUsers, setAllUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  
  const [searchQuery, setSearchQuery] = useState(""); 
  const [debouncedQuery, setDebouncedQuery] = useState(""); 
  const [selectedUserId, setSelectedUserId] = useState(""); 
  
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [totalItems, setTotalItems] = useState(0);

  // --- Terminate Dialog State ---
  const [jobToTerminate, setJobToTerminate] = useState<Job | null>(null);
  const [isTerminating, setIsTerminating] = useState(false);

  const skip = (currentPage - 1) * pageSize;

  useEffect(() => {
    const fetchUsers = async () => {
      try {
        const users = await client("/api/users");
        setAllUsers(users);
      } catch (e) {
        console.error("Failed to load users list", e);
      }
    };
    fetchUsers();
  }, []);

  const userFilterOptions = useMemo(() => {
    return [
      { 
        label: "All Users", 
        value: "", 
        icon: "/api/logo" 
      },
      ...allUsers.map(u => ({
        label: u.name,
        value: u.id,
        meta: u.email || "",
        icon: u.avatar_url 
      }))
    ];
  }, [allUsers]);

  const fetchJobs = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    
    try {
      const params = new URLSearchParams({
        skip: skip.toString(),
        limit: pageSize.toString(),
      });

      if (debouncedQuery.trim()) {
        params.append("search", debouncedQuery.trim());
      }

      if (selectedUserId) {
        params.append("creator_id", selectedUserId);
      }

      const data = await client(`/api/jobs?${params.toString()}`);
      setJobs(data.items);
      setTotalItems(data.total);

    } catch (e) {
      console.error("Backend offline?", e);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [skip, pageSize, debouncedQuery, selectedUserId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(searchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    setCurrentPage(1);
  }, [debouncedQuery, selectedUserId]);

  // Polling Mechanism
  useEffect(() => {
    fetchJobs();
    const intervalId = setInterval(() => {
      fetchJobs(true); 
    }, POLL_INTERVAL);
    return () => clearInterval(intervalId);
  }, [fetchJobs]); 

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
        taskName: job.task_name,
        description: job.description || "",
        namespace: job.namespace, 
        repoName: job.repo_name,
        branch: job.branch,
        commit_sha: job.commit_sha,
        entry_command: job.entry_command,
        gpu_count: job.gpu_count,
        gpu_type: job.gpu_type,
        job_type: job.job_type,
        cpu_count: job.cpu_count,
        memory_demand: job.memory_demand,
        runner: job.runner,
    });
    setIsDrawerOpen(true);
  };

  // 触发弹窗
  const onClickTerminate = (job: Job) => {
    setJobToTerminate(job);
  };

  // 执行终止
  const executeTermination = async () => {
    if (!jobToTerminate) return;
    setIsTerminating(true);
    try {
        await client(`/api/jobs/${jobToTerminate.id}/terminate`, { method: "POST" });
        fetchJobs(); 
        setJobToTerminate(null); // 关闭弹窗
    } catch (e) {
        alert("Failed to terminate job"); // 这里的错误处理还是保留简单的 alert，避免引入 Toast
        console.error(e);
    } finally {
        setIsTerminating(false);
    }
  };

  return (
    <div className="relative min-h-[calc(100vh-8rem)] pb-20"> 

      {/* 隐藏全局滚动条但保留滚动功能 */}
      <style jsx global>{`
        ::-webkit-scrollbar {
          display: none;
        }
        html {
          -ms-overflow-style: none;
          scrollbar-width: none;
        }
      `}</style>
      
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            Job Management
          </h1>
          <p className="text-zinc-500 text-sm mt-1">Monitor and schedule your training workloads.</p>
        </div>
        <button 
          onClick={handleNewJob} 
          className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50"
        >
          <Plus className="w-4 h-4" /> New Job
        </button>
      </div>

      {/* Filters & Search */}
      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-1.5 mb-6 flex items-center gap-2 backdrop-blur-sm relative z-20">
        <div className="relative flex-1 group">
           <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 group-focus-within:text-blue-500 transition-colors" />
           <input 
             type="text" 
             value={searchQuery}
             onChange={(e) => setSearchQuery(e.target.value)}
             placeholder="Search by Task Name or ID..." 
             className="w-full bg-transparent border-none py-2.5 pl-9 pr-4 text-sm text-zinc-200 focus:outline-none focus:ring-0 placeholder-zinc-600"
           />
        </div>
        <div className="h-6 w-px bg-zinc-800"></div>
        
        <div className="w-56"> 
          <SearchableSelect
             value={selectedUserId}
             onChange={setSelectedUserId}
             options={userFilterOptions}
             placeholder="Filter by User"
             className="mb-0 border-none bg-transparent" 
          />
        </div>
      </div>

      {/* Table & Pagination Container */}
      <div className="border border-zinc-800 rounded-xl bg-zinc-900/30 min-h-[400px] shadow-sm flex flex-col relative z-10">
        {loading ? (
           <div className="flex flex-col items-center justify-center flex-1 h-80 text-zinc-500 gap-3">
             <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
             <p className="text-sm font-medium">Fetching jobs...</p>
           </div>
        ) : jobs.length === 0 ? (
           <div className="flex flex-col items-center justify-center flex-1 h-80 text-zinc-500">
             <div className="w-16 h-16 bg-zinc-800/50 rounded-full flex items-center justify-center mb-4">
                <Box className="w-8 h-8 opacity-40" />
             </div>
             <p className="text-lg font-medium text-zinc-400">No jobs found</p>
             <p className="text-sm mt-1">Try adjusting your filters or create a new task.</p>
           </div>
        ) : (
          <>
            
            <div className="overflow-x-auto first:rounded-t-xl w-full">
              <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
                <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
                  <tr>
                    <th className="px-6 py-4 font-medium w-[21%]">Task / Task ID</th>
                    <th className="px-6 py-4 font-medium w-[8%] text-center">Priority</th>
                    <th className="px-6 py-4 font-medium w-[13%] text-center">Status</th>
                    <th className="px-6 py-4 font-medium w-[20%] text-center">Github Repo / Branch · Commit </th>
                    <th className="px-6 py-4 font-medium w-[13%] text-center">Resources</th>
                    <th className="px-6 py-4 font-medium w-[15%] text-center">Creator / Created at</th>
                    <th className="px-6 py-4 font-medium text-right w-[10%]"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/50">
                  {jobs.map((job) => {
                    // 判断是否显示终止按钮
                    const isActive = ['Pending', 'Running', 'Paused'].includes(job.status);
                    const isOwner = currentUser?.id === job.user?.id;
                    const canTerminate = isOwner && isActive;

                    return (
                      <tr 
                        key={job.id} 
                        onClick={() => router.push(`/jobs/${job.id}`)}
                        className="hover:bg-zinc-800/40 transition-colors group border-b border-zinc-800/50 last:border-0"
                      >
                        
                        {/* Task / Task ID */}
                        <td className="px-6 py-4 align-top whitespace-normal break-all">
                          <div className="flex flex-col gap-1.5">
                            <div className="flex items-center gap-2">
                                <CopyableText 
                                  text={job.task_name} 
                                  variant="text" 
                                  className="font-semibold text-zinc-200 text-base" 
                                />
                            </div>
                            <div className="flex items-center gap-2">
                              <CopyableText text={job.id} className="text-[10px] tracking-wider" />
                            </div>
                          </div>
                        </td>

                        {/* Priority */}
                        <td className="px-6 py-4 align-top text-center">
                            <JobPriorityBadge type={job.job_type} />
                        </td>

                        {/* Status */}
                        <td className="px-6 py-4 align-top text-center">
                          <JobStatusBadge status={job.status} />
                        </td>

                        {/* Github */}
                        <td className="px-6 py-4 align-top">
                            <div className="flex flex-col gap-1.5 items-center">
                                <span className="text-zinc-300 flex items-center gap-2 text-xs font-medium bg-zinc-900/50 w-fit px-2 py-1 rounded border border-zinc-800">
                                  <Box className="w-3.5 h-3.5 text-zinc-500"/> 
                                  {job.namespace} / {job.repo_name}
                                </span>
                                <div className="flex items-center gap-2 text-xs text-zinc-500 font-mono ml-1">
                                  <div className="w-1.5 h-1.5 rounded-full bg-zinc-600 flex-shrink-0"></div>
                                  <span 
                                    className="truncate max-w-[80px] sm:max-w-[140px] xl:max-w-[200px]" 
                                    title={job.branch}
                                  >
                                    {job.branch}
                                  </span>
                                  <span className="text-zinc-700 flex-shrink-0">|</span>
                                  <span className="bg-zinc-800 px-1.5 rounded text-zinc-400 flex-shrink-0">
                                    {job.commit_sha.substring(0, 7)}
                                  </span>
                                </div>
                            </div>
                        </td>

                        {/* Resources */}
                        <td className="px-6 py-4 align-top text-center">
                            <span className="text-zinc-300 text-sm font-medium">
                                {job.gpu_type === 'cpu' 
                                    ? 'cpu only'
                                    : `${job.gpu_type.replace(/_/g, ' ')} × ${job.gpu_count}`
                                }
                            </span>
                        </td>

                        {/* Creator */}
                        <td className="px-6 py-4 align-top">
                          <div className="flex justify-center">
                              <UserAvatar 
                                user={job.user} 
                                subText={formatBeijingTime(job.created_at)} 
                              />
                          </div>
                        </td>

                        {/* Actions */}
                        <td className="px-6 py-4 align-middle text-right">
                          <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all transform translate-x-2 group-hover:translate-x-0">
                            <button 
                                onClick={(e) => {
                                    e.stopPropagation(); 
                                    handleCloneJob(job); 
                                }}
                                className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm" 
                                title="Clone & Rerun"
                            >
                              <RefreshCw className="w-4 h-4" />
                            </button>

                            {canTerminate && (
                              <button 
                                  onClick={(e) => { 
                                    e.stopPropagation(); 
                                    onClickTerminate(job);
                                  }}
                                  className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30" 
                                  title="Terminate Job"
                              >
                                  <SquareX className="w-4 h-4" />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="px-6 bg-zinc-900/30 border-t border-zinc-800 last:rounded-b-xl">
              <PaginationControls 
                currentPage={currentPage}
                totalPages={Math.ceil(totalItems / pageSize)}
                pageSize={pageSize}
                totalItems={totalItems}
                onPageChange={setCurrentPage}
                onPageSizeChange={(newSize) => {
                   setPageSize(newSize);
                   setCurrentPage(1);
                }}
              />
            </div>
          </>
        )}
      </div>

      <JobDrawer
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        onSuccess={() => {
            setIsDrawerOpen(false);
            fetchJobs();
        }}
        mode={drawerMode}
        initialData={cloneData}
        formKey={drawerMode + (selectedJobId || "")}
      />

      {/* --- Confirmation Dialog --- */}
      <ConfirmationDialog
        isOpen={!!jobToTerminate}
        onClose={() => setJobToTerminate(null)}
        onConfirm={executeTermination}
        title="Terminate Task?"
        description={
          <span>
            Are you sure you want to terminate <strong>{jobToTerminate?.task_name}</strong>? 
            <br />
            This action will stop the process immediately and cannot be undone.
          </span>
        }
        confirmText="Terminate"
        variant="danger"
        isLoading={isTerminating}
      />
    </div>
  );
}
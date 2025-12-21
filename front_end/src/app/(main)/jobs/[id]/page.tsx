// front_end/src/app/(main)/jobs/[id]/page.tsx
"use client";

import { useState, useEffect, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Terminal, Clock, GitBranch, Cpu, Box, AlignLeft, RefreshCw } from "lucide-react";
import { client } from "@/lib/api";
import { CopyableText } from "@/components/ui/copyable-text";
import { POLL_INTERVAL } from "@/lib/config";
import { Job } from "@/types/job";
import { formatBeijingTime } from "@/lib/utils";
import { JobPriorityBadge } from "@/components/jobs/job-priority-badge";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import RenderMarkdown from "@/components/ui/render-markdown";
import { JobDrawer } from "@/components/jobs/job-drawer";
import { JobFormData } from "@/components/jobs/job-form";

export default function JobDetailsPage() {
  const params = useParams();
  const router = useRouter();
  const jobId = params.id as string;

  // --- 外部 slurm 任务无法浏览详情，在此拦截 ---
  if (decodeURIComponent(jobId).endsWith("(slurm)")) {
    return (
      <div className="flex flex-col items-center justify-center h-[60vh] text-zinc-400 gap-6">
        <div className="bg-zinc-900/50 p-6 rounded-2xl border border-zinc-800 text-center max-w-md">
          <div className="w-12 h-12 bg-zinc-800 rounded-full flex items-center justify-center mx-auto mb-4">
             <Terminal className="w-6 h-6 text-zinc-500" />
          </div>
          <h2 className="text-xl font-bold text-zinc-200 mb-2">External Task</h2>
          <p className="text-zinc-500 text-sm mb-6 leading-relaxed">
            This task is managed directly by Slurm CLI outside of Magnus. <br/>
            Detailed logs and configuration are not available here.
          </p>
          <button 
            onClick={() => router.back()} 
            className="px-6 py-2 bg-blue-600 text-white hover:bg-blue-500 text-sm font-medium rounded-lg transition-colors"
          >
            Go Back
          </button>
        </div>
      </div>
    );
  }
  
  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'console' | 'description'>('console');
  const logEndRef = useRef<HTMLDivElement>(null);

  // --- Clone State ---
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [cloneData, setCloneData] = useState<JobFormData | null>(null);
  // 新增：用于强制刷新 Form 的 Key
  const [cloneKey, setCloneKey] = useState(0);

  // 1. 获取任务详情 (支持轮询)
  useEffect(() => {
    const fetchJob = async (isBackground = false) => {
      if (!isBackground) setLoading(true);
      try {
        const data = await client(`/api/jobs/${jobId}`);
        setJob(data);
      } catch (e) {
        console.error("Failed to fetch job", e);
      } finally {
        if (!isBackground) setLoading(false);
      }
    };

    fetchJob();
    const interval = setInterval(() => fetchJob(true), POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [jobId]);

  // 2. 轮询日志
  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const res = await client(`/api/jobs/${jobId}/logs`);
        const logContent = typeof res === 'string' ? res : (res.logs || res.content || "");
        setLogs(logContent);
      } catch (e) {
        // 忽略错误
      }
    };

    fetchLogs();
    const interval = setInterval(fetchLogs, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [jobId]);

  const handleClone = () => {
    if (!job) return;
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
    // 强制 key 变化，触发 Form 重新挂载以读取 initialData
    setCloneKey(prev => prev + 1);
    setIsDrawerOpen(true);
  };

  if (loading) {
    return <div className="flex items-center justify-center h-[50vh] text-zinc-500">Loading Job Context...</div>;
  }

  if (!job) {
    return (
      <div className="flex flex-col items-center justify-center h-[50vh] text-zinc-500 gap-4">
        <p>Job not found</p>
        <button onClick={() => router.back()} className="text-blue-500 hover:underline">Go Back</button>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto pb-20">
      
      {/* 顶部导航 */}
      <div className="mb-8">
        <button 
          onClick={() => router.push('/jobs')} 
          className="flex items-center gap-2 text-zinc-400 hover:text-white transition-colors text-sm mb-6 group"
        >
          <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" />
          Back to Jobs
        </button>

        {/* 顶部 Header 区域 */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6">
          <div className="flex-1 min-w-0 pr-8"> 
            
            {/* 任务名 & 优先级 */}
            <div className="flex items-center gap-4 mb-3 group">
              <CopyableText 
                text={job.task_name} 
                variant="text"
                className="!w-auto text-3xl font-bold text-white tracking-tight leading-tight"
              />
              <div className="flex-shrink-0">
                  <JobPriorityBadge type={job.job_type} />
              </div>
            </div>
            
            {/* ID & 时间 */}
            <div className="flex items-center gap-1 text-sm text-zinc-500 font-mono">
              <div className="flex items-center gap-2">
                  <span className="text-zinc-600">ID:</span>
                  <CopyableText text={job.id} variant="id" />
              </div>
              <span className="text-zinc-700">|</span>
              <span className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                {formatBeijingTime(job.created_at)}
              </span>
            </div>
          
          </div>
          
          {/* 状态大卡片 */}
          <div className="flex items-center gap-4 bg-zinc-900/50 border border-zinc-800 px-6 py-4 rounded-xl backdrop-blur-sm flex-shrink-0 shadow-lg shadow-black/20">
            <JobStatusBadge status={job.status} size="md" />
            <div className="flex flex-col">
              <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">Status</span>
              <span className={`text-base font-bold tracking-wide
                ${job.status === 'Running' ? 'text-blue-400' : 
                  job.status === 'Success' ? 'text-green-400' :
                  job.status === 'Failed' ? 'text-red-400' : 'text-zinc-300'}`}>
                {job.status.toUpperCase()}
              </span>
            </div>
            {job.slurm_job_id && (
               <div className="ml-4 pl-6 border-l border-zinc-700/50 flex flex-col">
                  <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">Slurm ID</span>
                  <span className="text-base font-mono text-zinc-200">{job.slurm_job_id}</span>
               </div>
            )}
            
            {/* Clone Button - 位于状态卡片右侧，通过 border 分隔 */}
            <div className="ml-4 pl-4 border-l border-zinc-700/50 h-full flex items-center">
                <button
                    onClick={handleClone}
                    className="group flex items-center justify-center w-10 h-10 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700/50 hover:border-zinc-600 transition-all shadow-sm active:scale-95"
                    title="Clone this job"
                >
                    <RefreshCw className="w-5 h-5 text-zinc-400 group-hover:text-white transition-colors" />
                </button>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* 左侧：配置详情 - 使用 flex 布局对齐高度 */}
        <div className="lg:col-span-1 flex flex-col gap-6 lg:h-[650px]">
          
          {/* 代码信息 (固定高度，不压缩) */}
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
                        href={`https://github.com/${job.namespace}/${job.repo_name}`} 
                        target="_blank" 
                        rel="noopener noreferrer"
                        className="text-xs font-medium uppercase tracking-wider text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1 cursor-pointer transition-colors w-fit"
                        title="Open Repository in GitHub"
                    >
                        Github Repository
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                    </a>
                </div>
                
                <div className="flex items-center gap-2 text-sm text-zinc-200 bg-zinc-950 px-3 py-2 rounded-lg border border-zinc-800/50 shadow-inner">
                  <Box className="w-4 h-4 text-zinc-500 flex-shrink-0" />
                  <CopyableText 
                    text={`${job.namespace}/${job.repo_name}`} 
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
                            href={`https://github.com/${job.namespace}/${job.repo_name}/tree/${job.branch}`}
                            target="_blank" 
                            rel="noopener noreferrer"
                            className="text-xs font-medium uppercase tracking-wider text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1 cursor-pointer w-fit"
                            title="View Branch Tree"
                        >
                            Branch
                            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                        </a>
                    </div>
                    <div className="text-sm font-mono text-zinc-300 bg-zinc-950/50 px-2 py-1.5 rounded border border-zinc-800/50">
                        <CopyableText 
                          text={job.branch} 
                          variant="id" 
                          className="text-zinc-300"
                        />
                    </div>
                  </div>

                  {/* Commit SHA */}
                  <div>
                    <div className="flex items-center gap-2 mb-1.5">
                        <a 
                            href={`https://github.com/${job.namespace}/${job.repo_name}/commit/${job.commit_sha}`}
                            target="_blank" 
                            rel="noopener noreferrer"
                            className="text-xs font-medium uppercase tracking-wider text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1 cursor-pointer w-fit"
                            title="View Commit Details"
                        >
                            Commit SHA
                            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                        </a>
                    </div>
                    <div className="text-sm font-mono text-zinc-400 bg-zinc-950/50 px-2 py-1.5 rounded border border-zinc-800/50">
                        <CopyableText 
                          text={job.commit_sha} 
                          copyValue={job.commit_sha}
                          variant="id" 
                        />
                    </div>
                  </div>
              </div>
            </div>
          </div>

          {/* 资源配置 (固定高度，不压缩) */}
          <div className="shrink-0 bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-2">
              <Cpu className="w-4 h-4 text-zinc-400" />
              <h3 className="text-sm font-semibold text-zinc-200">Resources</h3>
            </div>
            <div className="p-5 grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1.5">Accelerator</label>
                <span className="text-base text-white font-medium block">
                   {job.gpu_type === 'CPU' ? 'CPU Only' : job.gpu_type}
                </span>
              </div>
              <div>
                <label className="text-xs text-zinc-500 font-medium uppercase tracking-wider block mb-1.5">Quantity</label>
                <span className="text-base text-white font-medium block">{job.gpu_count} GPUs</span>
              </div>
            </div>
          </div>

           {/* 入口命令 (自动填充剩余空间，高度对齐) */}
           <div className="flex-1 min-h-0 flex flex-col bg-zinc-900/30 border border-zinc-800 rounded-xl overflow-hidden">
            <div className="shrink-0 px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-2">
              <Terminal className="w-4 h-4 text-zinc-400" />
              <h3 className="text-sm font-semibold text-zinc-200">Entry Command</h3>
            </div>
            {/* 内容区域：Flex填充 + 滚动 */}
            <div className="flex-1 overflow-auto p-4 bg-zinc-950 custom-scrollbar">
              <CopyableText 
                text={job.entry_command} 
                variant="text" 
                className="text-xs font-mono text-green-400 leading-relaxed [&_span]:whitespace-pre-wrap"
              />
            </div>
          </div>

        </div>

        {/* 右侧：实时日志 & 描述 */}
        <div className="lg:col-span-2 flex flex-col h-[650px] bg-[#0c0c0e] border border-zinc-800 rounded-xl overflow-hidden shadow-2xl">
          
          {/* Tab Header - 纯文字，无按钮感 */}
          <div className="px-5 py-3 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between select-none">
            <div className="flex items-center gap-6">
              
              {/* Console Tab */}
              <div 
                onClick={() => setActiveTab('console')}
                className={`flex items-center gap-2 text-sm font-semibold transition-colors cursor-pointer
                  ${activeTab === 'console' ? 'text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'}`}
              >
                <Terminal className={`w-4 h-4 ${activeTab === 'console' ? 'text-zinc-400' : 'text-zinc-600'}`} />
                <span>Console Output</span>
                {/* 呼吸灯：只要是 Running 就显示，无论切到哪个 Tab */}
                {job.status === 'Running' && (
                  <span className="flex h-1.5 w-1.5 relative ml-0.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-green-500"></span>
                  </span>
                )}
              </div>

              {/* Description Tab */}
              <div 
                onClick={() => setActiveTab('description')}
                className={`flex items-center gap-2 text-sm font-semibold transition-colors cursor-pointer
                  ${activeTab === 'description' ? 'text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'}`}
              >
                <AlignLeft className={`w-4 h-4 ${activeTab === 'description' ? 'text-zinc-400' : 'text-zinc-600'}`} />
                <span>Description</span>
              </div>

            </div>
            
            {/* Live Indicator Text */}
            {job.status === 'Running' && (
               <div className="text-xs text-zinc-500 font-medium flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-500/50"></span>
                  Live
               </div>
            )}
          </div>
          
          {/* Content Area */}
          <div className="flex-1 overflow-auto p-5 custom-scrollbar">
            {activeTab === 'console' ? (
              <div className="font-mono text-xs leading-5">
                {logs ? (
                  <pre className="text-zinc-300 whitespace-pre-wrap break-all">
                    {logs}
                  </pre>
                ) : (
                   <div className="h-full flex flex-col items-center justify-center text-zinc-600 gap-3 min-h-[400px]">
                      <Terminal className="w-10 h-10 opacity-20" />
                      <p>
                        {['Pending', 'Running'].includes(job.status) 
                          ? "Waiting for output..." 
                          : "No output generated during execution"}
                      </p>
                   </div>
                )}
                <div ref={logEndRef} />
              </div>
            ) : (
              // Description Content (Markdown)
              <div className="min-h-[200px]">
                {job.description ? (
                  <RenderMarkdown content={job.description} />
                ) : (
                  <div className="h-full flex flex-col items-center justify-center text-zinc-600 gap-3 min-h-[200px] italic">
                    <AlignLeft className="w-8 h-8 opacity-20" />
                    No description provided for this task.
                  </div>
                )}
              </div>
            )}
          </div>

        </div>

      </div>

      <JobDrawer
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        onSuccess={() => {
            setIsDrawerOpen(false);
            router.push('/jobs');
        }}
        mode="clone"
        initialData={cloneData}
        formKey={`clone-${jobId}-${cloneKey}`} 
      />
    </div>
  );
}
// front_end/src/context/language-context.tsx
"use client";

import React, { createContext, useContext, useState, useEffect, useCallback } from "react";


export type Language = "zh" | "en";


const translations = {
  // ===== Common =====
  "common.cancel": { zh: "取消", en: "Cancel" },
  "common.confirm": { zh: "确认", en: "Confirm" },
  "common.delete": { zh: "删除", en: "Delete" },
  "common.edit": { zh: "编辑", en: "Edit" },
  "common.save": { zh: "保存", en: "Save" },
  "common.close": { zh: "关闭", en: "Close" },
  "common.loading": { zh: "加载中...", en: "Loading..." },
  "common.search": { zh: "搜索", en: "Search" },
  "common.noResults": { zh: "无结果", en: "No results" },
  "common.optional": { zh: "可选", en: "Optional" },
  "common.required": { zh: "必填", en: "Required" },
  "common.advanced": { zh: "高级", en: "Advanced" },
  "common.gotIt": { zh: "知道了", en: "Got it" },
  "common.help": { zh: "帮助", en: "Help" },
  "common.waiting": { zh: "等待中...", en: "Waiting..." },

  // ===== Auth =====
  "auth.signInWithFeishu": { zh: "飞书登录", en: "Sign in with Feishu" },
  "auth.logout": { zh: "退出登录", en: "Log out" },
  "auth.verifyingAccess": { zh: "正在验证权限...", en: "Verifying access..." },
  "auth.required": { zh: "需要登录", en: "Authentication Required" },
  "auth.requiredDesc": { zh: "您需要登录才能访问此资源。", en: "You need to be signed in to access this resource." },
  "auth.pleaseLogin": { zh: "请使用飞书账号登录以继续。", en: "Please login with your Feishu account to continue." },

  // ===== Header =====
  "header.hideToken": { zh: "隐藏令牌", en: "Hide Token" },
  "header.showToken": { zh: "显示令牌", en: "Show Token" },
  "header.resetToken": { zh: "重置令牌", en: "Reset Token" },
  "header.resetTokenTitle": { zh: "重置信任令牌？", en: "Reset Trust Token?" },
  "header.resetTokenDesc": { zh: "确定要重置信任令牌吗？", en: "Are you sure you want to reset your Trust Token?" },
  "header.resetTokenWarning": { zh: "当前令牌将立即失效。", en: "The current token will become invalid immediately." },
  "header.resetTokenNote": { zh: "您需要在集群上更新信任设置。", en: "You will need to update your trust settings on the cluster." },
  "header.noLoginToken": { zh: "未找到登录令牌。", en: "No login token found." },
  "header.refreshFailed": { zh: "刷新令牌失败：", en: "Failed to refresh token:" },

  // ===== Notifications =====
  "notifications.title": { zh: "通知", en: "Notifications" },
  "notifications.markRead": { zh: "标为已读", en: "Mark read" },
  "notifications.empty": { zh: "暂无通知", en: "No notifications yet" },
  "notifications.welcome": { zh: "欢迎使用 Magnus", en: "Welcome to Magnus" },
  "notifications.systemInit": { zh: "系统已初始化。您现在可以提交训练任务。", en: "System initialized. You can now submit training jobs." },

  // ===== Dashboard =====
  "dashboard.welcome": { zh: "欢迎回来，{name}。这是您的工作负载概览。", en: "Welcome back, {name}. Here is your workload overview." },
  "dashboard.totalOccupancy24h": { zh: "总占用 (24h)", en: "Total Occupancy (24h)" },
  "dashboard.allSlurmTasks": { zh: "所有 Slurm 任务", en: "All Slurm Tasks" },
  "dashboard.totalOccupancy7d": { zh: "总占用 (7d)", en: "Total Occupancy (7d)" },
  "dashboard.magnusUtil24h": { zh: "Magnus 利用率 (24h)", en: "Magnus Utilization (24h)" },
  "dashboard.magnusUtil7d": { zh: "Magnus 利用率 (7d)", en: "Magnus Utilization (7d)" },
  "dashboard.platformManaged": { zh: "平台管理（施工中）", en: "Platform Managed (WIP)" },
  "dashboard.myActiveJobs": { zh: "我的活跃任务", en: "My Active Jobs" },
  "dashboard.noActiveJobs": { zh: "暂无活跃任务。", en: "No active jobs." },
  "dashboard.newJob": { zh: "新建任务", en: "New Job" },

  // ===== Cluster =====
  "cluster.loadingStatus": { zh: "正在加载集群状态...", en: "Loading cluster status..." },
  "cluster.subtitle": { zh: "实时资源监控与队列状态。", en: "Real-time resource monitoring and queue status." },
  "cluster.availableGpus": { zh: "可用 GPU", en: "Available GPUs" },
  "cluster.activeJobs": { zh: "活跃任务", en: "Active Jobs" },
  "cluster.activeJobsDesc": { zh: "正在集群上执行", en: "Currently executing on cluster" },
  "cluster.queueDepth": { zh: "队列深度", en: "Queue Depth" },
  "cluster.queueDepthDesc": { zh: "等待资源的任务", en: "Jobs waiting for resources" },
  "cluster.runningJobs": { zh: "运行中的任务", en: "Running Jobs" },
  "cluster.noRunningJobs": { zh: "暂无运行中的任务。", en: "No running jobs." },
  "cluster.queuedJobs": { zh: "排队中的任务", en: "Queued Jobs" },
  "cluster.queueEmpty": { zh: "队列为空。", en: "Queue is empty." },

  // ===== Jobs =====
  "jobs.title": { zh: "任务管理", en: "Job Management" },
  "jobs.subtitle": { zh: "监控和调度您的训练工作负载。", en: "Monitor and schedule your training workloads." },
  "jobs.searchPlaceholder": { zh: "按任务名称或 ID 搜索...", en: "Search by Task Name or ID..." },
  "jobs.filterByUser": { zh: "按用户筛选", en: "Filter by User" },
  "jobs.newJob": { zh: "新建任务", en: "New Job" },
  "jobs.noJobsFound": { zh: "未找到任务", en: "No jobs found" },
  "jobs.fetchingJobs": { zh: "正在获取任务...", en: "Fetching jobs..." },
  "jobs.cloneRerun": { zh: "克隆并重新运行", en: "Clone & Rerun" },
  "jobs.terminateJob": { zh: "终止任务", en: "Terminate Job" },
  "jobs.cpuOnly": { zh: "仅 CPU", en: "cpu only" },
  "jobs.submitNewJob": { zh: "提交新任务", en: "Submit New Job" },
  "jobs.cloneJob": { zh: "克隆任务", en: "Clone Job" },
  "jobs.submitHelp": { zh: "任务提交帮助", en: "Job Submission Help" },

  // ===== Jobs Table Headers =====
  "jobs.table.task": { zh: "任务 / 任务 ID", en: "Task / Task ID" },
  "jobs.table.priority": { zh: "优先级", en: "Priority" },
  "jobs.table.status": { zh: "状态", en: "Status" },
  "jobs.table.repo": { zh: "Github 仓库 / 分支 · 提交", en: "Github Repo / Branch · Commit" },
  "jobs.table.resources": { zh: "资源", en: "Resources" },
  "jobs.table.creator": { zh: "创建者 / 创建时间", en: "Creator / Created at" },

  // ===== Job Form =====
  "jobForm.taskInfo": { zh: "任务信息", en: "Task Information" },
  "jobForm.taskName": { zh: "任务名称", en: "Task Name" },
  "jobForm.description": { zh: "描述", en: "Description" },
  "jobForm.codeSource": { zh: "代码来源", en: "Code Source" },
  "jobForm.namespace": { zh: "命名空间", en: "Namespace" },
  "jobForm.repoName": { zh: "仓库名称", en: "Repo Name" },
  "jobForm.scanRepo": { zh: "扫描仓库", en: "Scan Repository" },
  "jobForm.scanning": { zh: "扫描中...", en: "Scanning..." },
  "jobForm.branch": { zh: "分支", en: "Branch" },
  "jobForm.commit": { zh: "提交", en: "Commit" },
  "jobForm.latestCommit": { zh: "最新提交 (HEAD)", en: "Latest Commit (HEAD)" },
  "jobForm.useLatestCode": { zh: "使用最新代码", en: "Use latest code" },
  "jobForm.scheduling": { zh: "任务调度", en: "Job Scheduling" },
  "jobForm.priority": { zh: "任务优先级", en: "Job Priority" },
  "jobForm.computeResources": { zh: "计算资源", en: "Compute Resources" },
  "jobForm.gpuAccelerator": { zh: "GPU 加速器", en: "GPU Accelerator" },
  "jobForm.gpuCount": { zh: "GPU 数量", en: "GPU Count" },
  "jobForm.cpuCores": { zh: "CPU 核心数", en: "CPU Cores" },
  "jobForm.cpuCoresHint": { zh: "设为 0 使用分区默认值。", en: "Set to 0 to use partition default." },
  "jobForm.memory": { zh: "内存", en: "Memory" },
  "jobForm.memoryDefault": { zh: "默认：{value}", en: "Default: {value}" },
  "jobForm.runAsUser": { zh: "运行用户", en: "Run As User" },
  "jobForm.runAsUserDefault": { zh: "默认：{value}", en: "Default: {value}" },
  "jobForm.execution": { zh: "执行", en: "Execution" },
  "jobForm.entryCommand": { zh: "入口命令", en: "Entry Command" },
  "jobForm.waitingForLaunch": { zh: "等待启动", en: "Waiting for launch" },
  "jobForm.launchJob": { zh: "启动任务", en: "Launch Job" },
  "jobForm.reLaunch": { zh: "重新启动", en: "Re-Launch" },

  // ===== Job Priority Labels =====
  "priority.a1": { zh: "A1 - 高优稳定", en: "A1 - High Priority Stable" },
  "priority.a1.desc": { zh: "不可抢占 · 紧急", en: "Non-Preemptible • Urgent" },
  "priority.a2": { zh: "A2 - 次优稳定", en: "A2 - Medium Priority Stable" },
  "priority.a2.desc": { zh: "不可抢占", en: "Non-Preemptible" },
  "priority.b1": { zh: "B1 - 高优可抢", en: "B1 - High Priority Preemptible" },
  "priority.b1.desc": { zh: "可抢占（高）", en: "Preemptible (High)" },
  "priority.b2": { zh: "B2 - 次优可抢", en: "B2 - Low Priority Preemptible" },
  "priority.b2.desc": { zh: "可抢占（低）", en: "Preemptible (Low)" },

  // ===== Blueprints =====
  "blueprints.title": { zh: "Blueprints 注册表", en: "Blueprints Registry" },
  "blueprints.subtitle": { zh: "通过 Python 定义逻辑的标准化任务模板。", en: "Standardized task templates via Python-defined logic." },
  "blueprints.new": { zh: "新建 Blueprint", en: "New Blueprint" },
  "blueprints.searchPlaceholder": { zh: "搜索 Blueprints...", en: "Search Blueprints..." },
  "blueprints.filterByUser": { zh: "按用户筛选", en: "Filter by User" },
  "blueprints.deleteTitle": { zh: "删除 Blueprint", en: "Delete Blueprint" },
  "blueprints.deleteConfirm": { zh: "确定要删除 Blueprint {title} 吗？", en: "Are you sure you want to delete blueprint {title}?" },
  "blueprints.noFound": { zh: "未找到 Blueprints。", en: "No blueprints found." },
  "blueprints.fetching": { zh: "正在获取 Blueprints...", en: "Fetching blueprints..." },
  "blueprints.clone": { zh: "克隆", en: "Clone" },
  "blueprints.run": { zh: "运行", en: "Run" },

  // ===== Blueprints Table =====
  "blueprints.table.blueprint": { zh: "Blueprint / Blueprint ID", en: "Blueprint / Blueprint ID" },
  "blueprints.table.description": { zh: "描述", en: "Description" },
  "blueprints.table.author": { zh: "作者 / 更新时间", en: "Author / Updated at" },

  // ===== Services =====
  "services.title": { zh: "Service 注册表", en: "Service Registry" },
  "services.subtitle": { zh: "管理持久端点和弹性驱动。", en: "Manage persistent endpoints and elastic drivers." },
  "services.new": { zh: "新建 Service", en: "New Service" },
  "services.searchPlaceholder": { zh: "按服务名称或 ID 搜索...", en: "Search by Service Name or ID..." },
  "services.filterByOwner": { zh: "按所有者筛选", en: "Filter by Owner" },
  "services.sortLastActive": { zh: "排序：最后活跃", en: "Sort: Last Active" },
  "services.sortTraffic": { zh: "流量", en: "Traffic" },
  "services.sortUpdated": { zh: "排序：更新时间", en: "Sort: Updated" },
  "services.sortConfig": { zh: "配置", en: "Config" },
  "services.activeOnly": { zh: "仅活跃", en: "Active Only" },
  "services.activeOnlyTitle": { zh: "仅显示活跃服务", en: "Show active services only" },
  "services.deleteTitle": { zh: "删除 Service", en: "Delete Service" },
  "services.deleteConfirm": { zh: "确定要删除服务 {name} 吗？", en: "Are you sure you want to delete service {name}?" },
  "services.deleteWarning": { zh: "此操作不可撤销，将终止所有运行中的实例。", en: "This action cannot be undone and will terminate any running instances." },
  "services.stopTitle": { zh: "停止 Service", en: "Stop Service" },
  "services.stopConfirm": { zh: "确定要停止 {name} 吗？", en: "Are you sure you want to stop {name}?" },
  "services.stopWarning": { zh: "代理端点将停止接受流量。", en: "The proxy endpoint will stop accepting traffic." },
  "services.startTitle": { zh: "启动 Service", en: "Start Service" },
  "services.startConfirm": { zh: "确定要激活 {name} 吗？", en: "Are you sure you want to activate {name}?" },
  "services.startWarning": { zh: "这将启用流量路由并按需扩展资源。", en: "This will enable traffic routing and scale up resources on demand." },
  "services.noFound": { zh: "未找到服务。", en: "No services found." },
  "services.fetching": { zh: "正在获取服务...", en: "Fetching services..." },
  "services.inactive": { zh: "未激活", en: "Inactive" },
  "services.idle": { zh: "空闲", en: "Idle" },
  "services.editService": { zh: "编辑服务", en: "Edit Service" },
  "services.cloneService": { zh: "克隆服务", en: "Clone Service" },
  "services.noDescription": { zh: "暂无描述。", en: "No description provided." },

  // ===== Services Table =====
  "services.table.service": { zh: "服务 / 服务 ID", en: "Service / Service ID" },
  "services.table.description": { zh: "描述", en: "Description" },
  "services.table.jobStatus": { zh: "任务状态", en: "Job Status" },
  "services.table.manager": { zh: "管理者 / 更新时间", en: "Manager / Updated at" },

  // ===== Service Form =====
  "serviceForm.cloneUpdate": { zh: "克隆 / 更新服务", en: "Clone / Update Service" },
  "serviceForm.create": { zh: "创建服务", en: "Create Service" },
  "serviceForm.help": { zh: "弹性服务帮助", en: "Elastic Service Help" },
  "serviceForm.identity": { zh: "服务标识", en: "Service Identity" },
  "serviceForm.name": { zh: "服务名称", en: "SERVICE NAME" },
  "serviceForm.namePlaceholder": { zh: "我的交互环境", en: "My Interactive Environment" },
  "serviceForm.id": { zh: "服务 ID", en: "SERVICE ID" },
  "serviceForm.idPlaceholder": { zh: "jupyter-lab-01", en: "jupyter-lab-01" },
  "serviceForm.idHint": { zh: "唯一标识符（URL 安全）。", en: "Unique identifier (URL safe)." },
  "serviceForm.description": { zh: "描述", en: "DESCRIPTION" },
  "serviceForm.descPlaceholder": { zh: "服务描述（单行）", en: "Service description (Single line)" },
  "serviceForm.lifecycle": { zh: "生命周期与流量", en: "Lifecycle & Traffic" },
  "serviceForm.idleTimeout": { zh: "空闲超时（分钟）", en: "Idle Timeout (Mins)" },
  "serviceForm.idleTimeoutHint": { zh: "自动停止。0 表示禁用。", en: "Auto-stop. 0 to disable." },
  "serviceForm.reqTimeout": { zh: "请求超时（秒）", en: "Req Timeout (Secs)" },
  "serviceForm.reqTimeoutHint": { zh: "总处理超时。", en: "Total Handling Timeout." },
  "serviceForm.maxConcurrency": { zh: "最大并发", en: "Max Concurrency" },
  "serviceForm.maxConcurrencyHint": { zh: "最大并发请求数。", en: "Max In-flight Requests." },
  "serviceForm.underlyingJob": { zh: "底层任务配置", en: "Underlying Job Config" },
  "serviceForm.jobTaskName": { zh: "任务名称", en: "Job Task Name" },
  "serviceForm.jobDescription": { zh: "任务描述", en: "Job Description" },
  "serviceForm.jobDescPlaceholder": { zh: "工作进程描述...", en: "Worker process description..." },
  "serviceForm.persistentDriver": { zh: "持久服务驱动。", en: "Persistent service driver." },
  "serviceForm.updateService": { zh: "更新服务", en: "Update Service" },
  "serviceForm.cloneServiceBtn": { zh: "克隆服务", en: "Clone Service" },
  "serviceForm.createService": { zh: "创建服务", en: "Create Service" },

  // ===== Explorer =====
  "explorer.tagline1": { zh: "人机协作，", en: "Together With AI, " },
  "explorer.tagline2": { zh: "赋能科研", en: "For Our Research" },
  "explorer.uploading": { zh: "上传中...", en: "Uploading..." },
  "explorer.inputPlaceholder": { zh: "输入消息，可上传图片和文件", en: "Enter message, can upload images and files" },
  "explorer.privacyNotice": { zh: "您在 Magnus 平台上的活动记录会被收集并整理为科学语料，请注意隐私保护", en: "Your activity on Magnus Platform may be collected for research purposes. Please be mindful of privacy." },
  "explorer.sessions": { zh: "历史对话", en: "Explorer Sessions" },
  "explorer.noSessions": { zh: "暂无会话", en: "No sessions yet" },
  "explorer.shareSession": { zh: "分享对话", en: "Share Session" },
  "explorer.closeShare": { zh: "关闭分享", en: "Close Sharing" },
  "explorer.shareDesc": { zh: "开启后，组织内的成员可通过以下链接查看该对话。", en: "Once enabled, organization members can view this session via the link below." },
  "explorer.sharedDesc": { zh: "组织内的成员可通过链接查看该对话。", en: "Organization members can view this session via the link." },
  "explorer.enableShare": { zh: "开启分享", en: "Enable Sharing" },
  "explorer.disableShare": { zh: "停止分享", en: "Disable Sharing" },

  // ===== Pagination =====
  "pagination.showing": { zh: "显示", en: "Showing" },
  "pagination.of": { zh: "共", en: "of" },
  "pagination.rows": { zh: "行数：", en: "Rows:" },

  // ===== Validation Errors =====
  "validation.taskNameRequired": { zh: "任务名称为必填项", en: "Task name is required" },
  "validation.namespaceRequired": { zh: "命名空间为必填项", en: "Namespace is required" },
  "validation.repoRequired": { zh: "仓库名称为必填项", en: "Repository name is required" },
  "validation.branchRequired": { zh: "分支为必填项", en: "Branch is required" },
  "validation.commandRequired": { zh: "入口命令为必填项", en: "Entry command is required" },
  "validation.serviceNameRequired": { zh: "服务名称为必填项", en: "Service name is required" },
  "validation.serviceIdRequired": { zh: "服务 ID 为必填项", en: "Service ID is required" },
  "validation.serviceIdInvalid": { zh: "服务 ID 只能包含小写字母、数字和连字符", en: "Service ID can only contain lowercase letters, numbers, and hyphens" },
} as const;


type TranslationKey = keyof typeof translations;


interface LanguageContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: TranslationKey, params?: Record<string, string>) => string;
}


const LanguageContext = createContext<LanguageContextType | undefined>(undefined);


export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>("zh");


  useEffect(() => {
    const saved = localStorage.getItem("magnus_language") as Language | null;
    if (saved && (saved === "zh" || saved === "en")) {
      setLanguageState(saved);
      return;
    }

    // Default to Chinese
    setLanguageState("zh");
  }, []);


  const setLanguage = useCallback((lang: Language) => {
    setLanguageState(lang);
    localStorage.setItem("magnus_language", lang);
  }, []);


  const t = useCallback((key: TranslationKey, params?: Record<string, string>): string => {
    const translation = translations[key];
    if (!translation) {
      console.warn(`Missing translation for key: ${key}`);
      return key;
    }

    let text: string = translation[language];

    if (params) {
      Object.entries(params).forEach(([paramKey, value]) => {
        text = text.replace(`{${paramKey}}`, value);
      });
    }

    return text;
  }, [language]);


  return (
    <LanguageContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </LanguageContext.Provider>
  );
}


export function useLanguage() {
  const context = useContext(LanguageContext);
  if (context === undefined) {
    throw new Error("useLanguage must be used within a LanguageProvider");
  }
  return context;
}

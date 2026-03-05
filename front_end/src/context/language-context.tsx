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
  "common.error": { zh: "错误", en: "Error" },
  "common.ok": { zh: "知道了", en: "OK" },
  "common.operationFailed": { zh: "操作失败", en: "Operation Failed" },
  "common.typeToConfirm": { zh: "请输入 {v} 以确认", en: "Type {v} to confirm" },
  "common.allUsers": { zh: "所有用户", en: "All Users" },
  "common.wip": { zh: "施工中", en: "Under Construction" },

  // ===== Navigation =====
  "nav.explorer": { zh: "启新", en: "Explorer" },
  "nav.people": { zh: "人事", en: "People" },
  "nav.motions": { zh: "共决", en: "Motions" },
  "nav.jobs": { zh: "任务", en: "Jobs" },
  "nav.blueprints": { zh: "蓝图", en: "Blueprints" },
  "nav.services": { zh: "服务", en: "Services" },
  "nav.skills": { zh: "技能", en: "Skills" },
  "nav.images": { zh: "镜像", en: "Images" },
  "nav.cluster": { zh: "集群", en: "Cluster" },

  // ===== Auth =====
  "auth.signInWithFeishu": { zh: "飞书登录", en: "Sign in with Feishu" },
  "auth.logout": { zh: "退出登录", en: "Log out" },
  "auth.verifyingAccess": { zh: "正在验证权限...", en: "Verifying access..." },
  "auth.required": { zh: "需要登录", en: "Authentication Required" },
  "auth.requiredDesc": { zh: "您需要登录才能访问此资源。", en: "You need to be signed in to access this resource." },
  "auth.pleaseLogin": { zh: "请使用飞书账号登录以继续。", en: "Please login with your Feishu account to continue." },
  "auth.authenticating": { zh: "正在通过飞书登录...", en: "Authenticating with Feishu..." },
  "auth.loginFailed": { zh: "登录失败", en: "Login Failed" },
  "auth.backToHome": { zh: "返回首页", en: "Back to Home" },

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
  "header.editToken": { zh: "自定义令牌", en: "Edit Token" },
  "header.customTokenLabel": { zh: "输入自定义令牌", en: "Enter custom token" },
  "header.customTokenWarning": { zh: "不建议普通用户使用此功能。", en: "This feature is not recommended for regular users." },
  "header.customTokenInvalid": { zh: "令牌必须以 sk- 开头且长度为 35", en: "Token must start with sk- and be 35 characters" },
  "header.saveCustomToken": { zh: "保存令牌", en: "Save Token" },

  // ===== Notifications =====
  "notifications.title": { zh: "通知", en: "Notifications" },
  "notifications.markRead": { zh: "标为已读", en: "Mark read" },
  "notifications.empty": { zh: "暂无通知", en: "No notifications yet" },
  "notifications.welcome": { zh: "欢迎使用 Magnus", en: "Welcome to Magnus" },
  "notifications.systemInit": { zh: "系统已初始化。您现在可以提交训练任务。", en: "System initialized. You can now submit training jobs." },

  // ===== Cluster =====
  "cluster.welcome": { zh: "欢迎回来，{name}。这是集群概览。", en: "Welcome back, {name}. Here is your cluster overview." },
  "cluster.newJob": { zh: "新建任务", en: "New Job" },
  "cluster.myActiveJobs": { zh: "我和下属的活跃任务", en: "My & Team Active Jobs" },
  "cluster.noActiveJobs": { zh: "暂无活跃任务。", en: "No active jobs." },
  "cluster.availableGpus": { zh: "可用 GPU", en: "Available GPUs" },
  "cluster.availableCpuMem": { zh: "CPU / 内存", en: "CPUs / Memory" },
  "cluster.cores": { zh: "核心", en: "cores" },
  "cluster.activeJobs": { zh: "活跃任务", en: "Active Jobs" },
  "cluster.activeJobsDesc": { zh: "正在集群上执行", en: "Currently executing on cluster" },
  "cluster.queueDepth": { zh: "队列深度", en: "Queue Depth" },
  "cluster.queueDepthDesc": { zh: "等待资源的任务", en: "Jobs waiting for resources" },
  "cluster.runningJobs": { zh: "运行中的任务", en: "Running Jobs" },
  "cluster.noRunningJobs": { zh: "暂无运行中的任务。", en: "No running jobs." },
  "cluster.queuedJobs": { zh: "排队或准备中的任务", en: "Queued or Preparing Jobs" },
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
  "jobForm.cpuCoresHint": { zh: "设为 0 使用默认值 ({value})。", en: "Set to 0 for default ({value})." },
  "jobForm.memory": { zh: "内存", en: "Memory" },
  "jobForm.memoryDefault": { zh: "默认：{value}", en: "Default: {value}" },
  "jobForm.ephemeralStorage": { zh: "临时存储", en: "Ephemeral Storage" },
  "jobForm.ephemeralStorageDefault": { zh: "默认：{value}", en: "Default: {value}" },
  "jobForm.runAsUser": { zh: "运行用户", en: "Run As User" },
  "jobForm.runAsUserDefault": { zh: "默认：{value}", en: "Default: {value}" },
  "jobForm.containerImage": { zh: "容器镜像", en: "Container Image" },
  "jobForm.containerImageDefault": { zh: "默认：{value}", en: "Default: {value}" },
  "jobForm.systemEntryCommand": { zh: "系统入口指令", en: "System Entry Command" },
  "jobForm.systemEntryCommandDefault": { zh: "留空使用默认值", en: "Leave empty for default" },
  "jobForm.execution": { zh: "执行", en: "Execution" },
  "jobForm.entryCommand": { zh: "入口指令", en: "Entry Command" },
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
  "blueprints.title": { zh: "蓝图注册表", en: "Blueprints Registry" },
  "blueprints.subtitle": { zh: "通过 Python 定义逻辑的标准化任务模板。", en: "Standardized task templates via Python-defined logic." },
  "blueprints.new": { zh: "新建蓝图", en: "New Blueprint" },
  "blueprints.searchPlaceholder": { zh: "搜索蓝图...", en: "Search Blueprints..." },
  "blueprints.filterByUser": { zh: "按用户筛选", en: "Filter by User" },
  "blueprints.deleteTitle": { zh: "删除蓝图", en: "Delete Blueprint" },
  "blueprints.deleteConfirm": { zh: "确定要删除蓝图「{title}」吗？", en: "Are you sure you want to delete blueprint {title}?" },
  "blueprints.noFound": { zh: "未找到蓝图。", en: "No blueprints found." },
  "blueprints.fetching": { zh: "正在获取蓝图...", en: "Fetching blueprints..." },
  "blueprints.clone": { zh: "克隆", en: "Clone" },
  "blueprints.run": { zh: "运行", en: "Run" },

  // ===== Blueprints Table =====
  "blueprints.table.blueprint": { zh: "蓝图 / 蓝图 ID", en: "Blueprint / Blueprint ID" },
  "blueprintEditor.nameRequired": { zh: "蓝图名称是必填的", en: "Blueprint Name is required" },
  "blueprintEditor.idRequired": { zh: "蓝图 ID 是必填的", en: "Blueprint ID is required" },
  "blueprintEditor.descriptionRequired": { zh: "蓝图描述是必填的", en: "Description is required" },
  "blueprints.table.description": { zh: "描述", en: "Description" },
  "blueprints.table.author": { zh: "作者 / 更新时间", en: "Author / Updated at" },
  "blueprints.table.noDescription": { zh: "暂无描述", en: "No description" },

  // ===== Services =====
  "services.title": { zh: "服务注册表", en: "Service Registry" },
  "services.subtitle": { zh: "管理持久端点和弹性驱动。", en: "Manage persistent endpoints and elastic drivers." },
  "services.new": { zh: "新建服务", en: "New Service" },
  "services.searchPlaceholder": { zh: "按服务名称或 ID 搜索...", en: "Search by Service Name or ID..." },
  "services.filterByOwner": { zh: "按所有者筛选", en: "Filter by Owner" },
  "services.sortLastActive": { zh: "排序：最后活跃", en: "Sort: Last Active" },
  "services.sortTraffic": { zh: "流量", en: "Traffic" },
  "services.sortUpdated": { zh: "排序：更新时间", en: "Sort: Updated" },
  "services.sortConfig": { zh: "配置", en: "Config" },
  "services.activeOnly": { zh: "仅活跃", en: "Active Only" },
  "services.activeOnlyTitle": { zh: "仅显示活跃服务", en: "Show active services only" },
  "services.deleteTitle": { zh: "删除服务", en: "Delete Service" },
  "services.deleteConfirm": { zh: "确定要删除服务 {name} 吗？", en: "Are you sure you want to delete service {name}?" },
  "services.deleteWarning": { zh: "此操作不可撤销，将终止所有运行中的实例。", en: "This action cannot be undone and will terminate any running instances." },
  "services.stopTitle": { zh: "停止服务", en: "Stop Service" },
  "services.stopConfirm": { zh: "确定要停止 {name} 吗？", en: "Are you sure you want to stop {name}?" },
  "services.stopWarning": { zh: "代理端点将停止接受流量。", en: "The proxy endpoint will stop accepting traffic." },
  "services.startTitle": { zh: "启动服务", en: "Start Service" },
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
  "explorer.tagline1": { zh: "人机协作，", en: "Automating " },
  "explorer.tagline2": { zh: "赋能科研", en: "Discoveries" },
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
  "explorer.deleteSession": { zh: "删除对话", en: "Delete Session" },
  "explorer.deleteDesc": { zh: "确定要删除这个对话吗？此操作不可撤销。", en: "Are you sure you want to delete this session? This action cannot be undone." },
  "explorer.confirmDelete": { zh: "确认删除", en: "Confirm Delete" },
  "explorer.notFound": { zh: "会话不存在", en: "Session Not Found" },
  "explorer.notFoundDesc": { zh: "该会话不存在或已被删除。", en: "This session does not exist or has been deleted." },
  "explorer.returnToExplorer": { zh: "返回启新", en: "Return to Explorer" },

  // ===== Pagination =====
  "pagination.showing": { zh: "显示", en: "Showing" },
  "pagination.of": { zh: "共", en: "of" },
  "pagination.rows": { zh: "行数：", en: "Rows:" },

  // ===== Validation Errors =====
  "validation.taskNameRequired": { zh: "任务名称为必填项", en: "Task name is required" },
  "validation.namespaceRequired": { zh: "命名空间为必填项", en: "Namespace is required" },
  "validation.repoRequired": { zh: "仓库名称为必填项", en: "Repository name is required" },
  "validation.branchRequired": { zh: "分支为必填项", en: "Branch is required" },
  "validation.commandRequired": { zh: "入口指令为必填项", en: "Entry command is required" },
  "validation.serviceNameRequired": { zh: "服务名称为必填项", en: "Service name is required" },
  "validation.serviceIdRequired": { zh: "服务 ID 为必填项", en: "Service ID is required" },
  "validation.serviceIdInvalid": { zh: "服务 ID 只能包含小写字母、数字和连字符", en: "Service ID can only contain lowercase letters, numbers, and hyphens" },

  // ===== Blueprint Editor =====
  "blueprintEditor.create": { zh: "创建蓝图", en: "Create Blueprint" },
  "blueprintEditor.cloneUpdate": { zh: "克隆 / 更新蓝图", en: "Clone / Update Blueprint" },
  "blueprintEditor.help": { zh: "蓝图编辑帮助", en: "Blueprint Editor Help" },
  "blueprintEditor.basicInfo": { zh: "基本信息", en: "Basic Information" },
  "blueprintEditor.name": { zh: "蓝图名称", en: "Blueprint Name" },
  "blueprintEditor.id": { zh: "蓝图 ID", en: "Blueprint ID" },
  "blueprintEditor.idHint": { zh: "唯一标识符（URL 安全）。", en: "Unique identifier (URL safe)." },
  "blueprintEditor.implementation": { zh: "实现逻辑", en: "Implementation" },
  "blueprintEditor.pythonLogic": { zh: "Python 逻辑", en: "Python Logic" },
  "blueprintEditor.updating": { zh: "正在更新现有蓝图。", en: "Updating existing blueprint." },
  "blueprintEditor.saved": { zh: "已保存", en: "Saved" },
  "blueprintEditor.creating": { zh: "正在创建新蓝图定义。", en: "Creating new blueprint definition." },
  "blueprintEditor.updateBtn": { zh: "更新蓝图", en: "Update Blueprint" },
  "blueprintEditor.createBtn": { zh: "创建蓝图", en: "Create Blueprint" },
  "blueprintEditor.cloneBtn": { zh: "克隆蓝图", en: "Clone Blueprint" },

  // ===== Blueprint Runner =====
  "blueprintRunner.help": { zh: "蓝图运行帮助", en: "Blueprint Runner Help" },
  "blueprintRunner.launching": { zh: "启动中...", en: "Launching..." },
  "blueprintRunner.launch": { zh: "启动", en: "Launch" },
  "blueprintRunner.loadFailed": { zh: "加载蓝图失败", en: "Failed to load blueprint" },

  // ===== Blueprint Detail Page =====
  "blueprintDetail.backTo": { zh: "返回蓝图", en: "Back to Blueprints" },
  "blueprintDetail.notFound": { zh: "蓝图未找到", en: "Blueprint Not Found" },
  "blueprintDetail.notFoundDesc": { zh: "在注册表中未找到蓝图「{id}」。它可能已被删除或 ID 不正确。", en: "The blueprint definition {id} could not be located in the registry. It may have been deleted or the ID is incorrect." },
  "blueprintDetail.returnToRegistry": { zh: "返回注册表", en: "Return to Registry" },
  "blueprintDetail.author": { zh: "作者", en: "Author" },
  "blueprintDetail.editClone": { zh: "编辑 / 克隆蓝图", en: "Edit / Clone Blueprint" },
  "blueprintDetail.runBlueprint": { zh: "运行蓝图", en: "Run Blueprint" },
  "blueprintDetail.deleteBlueprint": { zh: "删除蓝图", en: "Delete Blueprint" },
  "blueprintDetail.description": { zh: "描述", en: "Description" },
  "blueprintDetail.copyDescription": { zh: "复制描述", en: "Copy Description" },
  "blueprintDetail.implementationLogic": { zh: "实现逻辑", en: "Implementation Logic" },
  "blueprintDetail.copyCode": { zh: "复制代码", en: "Copy Code" },
  "blueprintDetail.configuration": { zh: "配置", en: "Configuration" },
  "blueprintDetail.configureParams": { zh: "配置参数以实例化此任务。", en: "Configure parameters to instantiate this task." },
  "blueprintDetail.deleteConfirmDesc": { zh: "确定要删除蓝图「{title}」吗？此操作不可撤销。", en: "Are you sure you want to delete blueprint {title}? This action cannot be undone." },

  // ===== Job Detail Page =====
  "jobDetail.backTo": { zh: "返回任务", en: "Back to Jobs" },
  "jobDetail.cloneJob": { zh: "克隆此任务", en: "Clone this job" },
  "jobDetail.terminateTask": { zh: "终止任务", en: "Terminate Task" },
  "jobDetail.openRepoGithub": { zh: "在 GitHub 中打开仓库", en: "Open Repository in GitHub" },
  "jobDetail.viewBranchTree": { zh: "查看分支树", en: "View Branch Tree" },
  "jobDetail.viewCommitDetails": { zh: "查看提交详情", en: "View Commit Details" },
  "jobDetail.copyFullCommand": { zh: "复制完整命令", en: "Copy Full Command" },
  "jobDetail.firstPage": { zh: "第一页", en: "First Page" },
  "jobDetail.prevPage": { zh: "上一页", en: "Previous Page" },
  "jobDetail.nextPage": { zh: "下一页", en: "Next Page" },
  "jobDetail.lastPage": { zh: "最后一页", en: "Last Page" },
  "jobDetail.followMode": { zh: "跟随模式（双击启用）", en: "Following (Double-click to enable)" },
  "jobDetail.lastPageFollow": { zh: "最后一页（双击跟随）", en: "Last Page (Double-click to follow)" },

  // ===== Service Detail Page =====
  "serviceDetail.backTo": { zh: "返回服务", en: "Back to Services" },
  "serviceDetail.editClone": { zh: "编辑 / 克隆服务", en: "Edit / Clone Service" },
  "serviceDetail.stopService": { zh: "停止服务", en: "Stop Service" },
  "serviceDetail.startService": { zh: "启动服务", en: "Start Service" },
  "serviceDetail.deleteService": { zh: "删除服务", en: "Delete Service" },
  "serviceDetail.copyDescription": { zh: "复制描述", en: "Copy Description" },
  "serviceDetail.openRepoGithub": { zh: "在 GitHub 中打开仓库", en: "Open Repository in GitHub" },
  "serviceDetail.viewBranchTree": { zh: "查看分支树", en: "View Branch Tree" },
  "serviceDetail.viewCommitDetails": { zh: "查看提交详情", en: "View Commit Details" },
  "serviceDetail.copyFullCommand": { zh: "复制完整命令", en: "Copy Full Command" },

  // ===== Job Operations =====
  "jobOps.terminateTitle": { zh: "终止任务？", en: "Terminate Task?" },
  "jobOps.terminateDesc": { zh: "确定要终止 {name} 吗？此操作将立即停止进程且不可撤销。", en: "Are you sure you want to terminate {name}? This action will stop the process immediately and cannot be undone." },
  "jobOps.terminateBtn": { zh: "终止", en: "Terminate" },
  "jobOps.terminateFailed": { zh: "终止任务失败", en: "Failed to terminate job" },

  // ===== Common Actions =====
  "action.copy": { zh: "复制", en: "Copy" },
  "action.copied": { zh: "已复制！", en: "Copied!" },
  "action.copyConfig": { zh: "复制配置", en: "Copy Config" },
  "action.pasteConfig": { zh: "粘贴配置", en: "Paste Config" },

  // ===== Explorer Session =====
  "explorer.goodResponse": { zh: "好的回复", en: "Good response" },
  "explorer.badResponse": { zh: "不好的回复", en: "Bad response" },
  "explorer.regenerate": { zh: "重新生成", en: "Regenerate" },
  "explorer.edit": { zh: "编辑", en: "Edit" },
  "explorer.thinking": { zh: "思考中", en: "Thinking" },
  "explorer.thinkingWait": { zh: "思考中...", en: "Thinking..." },
  "explorer.send": { zh: "发送", en: "Send" },
  "explorer.voiceStart": { zh: "语音输入", en: "Voice input" },
  "explorer.voiceStop": { zh: "停止录音", en: "Stop recording" },
  "explorer.voiceTranscribing": { zh: "识别中...", en: "Transcribing..." },

  // ===== Job Detail - Navigation =====
  "jobDetail.backToService": { zh: "返回服务", en: "Back to Service" },
  "jobDetail.backToServices": { zh: "返回服务", en: "Back to Services" },
  "jobDetail.backToCluster": { zh: "返回集群", en: "Back to Cluster" },
  "jobDetail.backToJobs": { zh: "返回任务", en: "Back to Jobs" },

  // ===== Job Detail - States =====
  "jobDetail.loading": { zh: "正在加载任务上下文...", en: "Loading Job Context..." },
  "jobDetail.notFound": { zh: "任务不存在", en: "Job Not Found" },
  "jobDetail.notFoundDesc": { zh: "该任务不存在或已被删除。", en: "This job does not exist or has been deleted." },
  "jobDetail.returnToJobs": { zh: "返回任务列表", en: "Return to Jobs" },
  "jobDetail.goBack": { zh: "返回", en: "Go Back" },

  // ===== Job Detail - External Task =====
  "jobDetail.externalTask": { zh: "外部任务", en: "External Task" },
  "jobDetail.externalTaskDesc": { zh: "此任务由 Slurm CLI 直接管理，不在 Magnus 范围内。详细日志和配置在此不可用。", en: "This task is managed directly by Slurm CLI outside of Magnus. Detailed logs and configuration are not available here." },

  // ===== Job Detail - Status Card =====
  // ===== Job Status =====
  "jobStatus.running": { zh: "运行中", en: "Running" },
  "jobStatus.success": { zh: "成功", en: "Success" },
  "jobStatus.failed": { zh: "失败", en: "Failed" },
  "jobStatus.paused": { zh: "已暂停", en: "Paused" },
  "jobStatus.pending": { zh: "排队中", en: "Pending" },
  "jobStatus.preparing": { zh: "准备中", en: "Preparing" },
  "jobStatus.terminated": { zh: "已终止", en: "Terminated" },

  // ===== Job Detail - Header =====
  "jobDetail.status": { zh: "状态", en: "Status" },
  "jobDetail.creator": { zh: "创建者", en: "Creator" },
  "jobDetail.live": { zh: "实时", en: "Live" },

  // ===== Job Detail - Repository Section =====
  "jobDetail.repository": { zh: "仓库", en: "Repository" },
  "jobDetail.githubRepo": { zh: "Github 仓库", en: "Github Repository" },
  "jobDetail.branch": { zh: "分支", en: "Branch" },
  "jobDetail.commitSha": { zh: "提交 SHA", en: "Commit SHA" },

  // ===== Job Detail - Resources Section =====
  "jobDetail.resources": { zh: "资源", en: "Resources" },
  "jobDetail.accelerator": { zh: "加速器", en: "Accelerator" },
  "jobDetail.cpuOnly": { zh: "仅 CPU", en: "CPU Only" },
  "jobDetail.gpuCount": { zh: "GPU 数量", en: "GPU Count" },
  "jobDetail.cpuCores": { zh: "CPU 核心", en: "CPU Cores" },
  "jobDetail.memory": { zh: "内存", en: "Memory" },
  "jobDetail.stationDefault": { zh: "（分区默认）", en: "(Station Default)" },

  // ===== Job Detail - Entry Command =====
  "jobDetail.entryCommand": { zh: "入口指令", en: "Entry Command" },

  // ===== Job Detail - Execution Environment =====
  "jobDetail.executionEnvironment": { zh: "执行环境", en: "Execution Environment" },
  "jobDetail.containerImage": { zh: "容器镜像", en: "Container Image" },
  "jobDetail.systemEntryCommand": { zh: "系统入口指令", en: "System Entry Command" },

  // ===== Job Detail - Tabs =====
  "jobDetail.consoleOutput": { zh: "控制台输出", en: "Console Output" },
  "jobDetail.description": { zh: "描述", en: "Description" },
  "jobDetail.metrics": { zh: "指标", en: "Metrics" },

  // ===== Job Detail - Console =====
  "jobDetail.waitingOutput": { zh: "等待输出...", en: "Waiting for output..." },
  "jobDetail.noOutput": { zh: "执行期间未产生输出", en: "No output generated during execution" },
  "jobDetail.noDescriptionProvided": { zh: "未提供描述。", en: "No description provided." },

  // ===== Job Detail - Metrics =====
  "jobDetail.comingSoon": { zh: "即将推出", en: "Coming Soon" },
  "jobDetail.underConstruction": { zh: "施工中...", en: "Under construction..." },

  // ===== Service Detail - Not Found =====
  "serviceDetail.notFound": { zh: "服务未找到", en: "Service Not Found" },
  "serviceDetail.notFoundDesc": { zh: "在注册表中未找到服务 {id}。它可能已被删除或 ID 不正确。", en: "The service {id} could not be located in the registry. It may have been deleted or the ID is incorrect." },
  "serviceDetail.returnToServices": { zh: "返回服务", en: "Return to Services" },

  // ===== Service Detail - Status =====
  "serviceDetail.status": { zh: "状态", en: "Status" },
  "serviceDetail.inactive": { zh: "未激活", en: "Inactive" },
  "serviceDetail.idle": { zh: "空闲", en: "Idle" },
  "serviceDetail.manager": { zh: "管理者", en: "Manager" },

  // ===== Service Detail - Sections =====
  "serviceDetail.description": { zh: "描述", en: "Description" },
  "serviceDetail.noDescription": { zh: "未提供描述。", en: "No description provided." },
  "serviceDetail.serviceConfig": { zh: "服务配置", en: "Service Configuration" },
  "serviceDetail.requestTimeout": { zh: "请求超时", en: "Request Timeout" },
  "serviceDetail.idleTimeout": { zh: "空闲超时", en: "Idle Timeout" },
  "serviceDetail.neverScaleDown": { zh: "（永不缩容）", en: "(Never Scale Down)" },
  "serviceDetail.maxConcurrency": { zh: "最大并发", en: "Max Concurrency" },
  "serviceDetail.jobType": { zh: "任务类型", en: "Job Type" },

  // ===== Service Detail - Repository =====
  "serviceDetail.repository": { zh: "仓库", en: "Repository" },
  "serviceDetail.githubRepo": { zh: "Github 仓库", en: "Github Repository" },
  "serviceDetail.branch": { zh: "分支", en: "Branch" },
  "serviceDetail.commitSha": { zh: "提交 SHA", en: "Commit SHA" },

  // ===== Service Detail - Resources =====
  "serviceDetail.resources": { zh: "资源", en: "Resources" },
  "serviceDetail.accelerator": { zh: "加速器", en: "Accelerator" },
  "serviceDetail.cpuOnly": { zh: "仅 CPU", en: "CPU Only" },
  "serviceDetail.gpuCount": { zh: "GPU 数量", en: "GPU Count" },
  "serviceDetail.cpuCores": { zh: "CPU 核心", en: "CPU Cores" },
  "serviceDetail.memory": { zh: "内存", en: "Memory" },
  "serviceDetail.stationDefault": { zh: "（分区默认）", en: "(Station Default)" },

  // ===== Service Detail - Entry Command =====
  "serviceDetail.entryCommand": { zh: "入口指令", en: "Entry Command" },

  // ===== Service Detail - Dialogs =====
  "serviceDetail.deleteTitle": { zh: "删除服务", en: "Delete Service" },
  "serviceDetail.deleteDesc": { zh: "确定要删除服务 {name} 吗？此操作不可撤销，将终止所有运行中的实例。", en: "Are you sure you want to delete service {name}? This action cannot be undone and will terminate any running instances." },
  "serviceDetail.deleteConfirm": { zh: "删除服务", en: "Delete Service" },
  "serviceDetail.stopTitle": { zh: "停止服务", en: "Stop Service" },
  "serviceDetail.stopDesc": { zh: "确定要停止 {name} 吗？代理端点将停止接受流量。", en: "Are you sure you want to stop {name}? The proxy endpoint will stop accepting traffic." },
  "serviceDetail.stopConfirm": { zh: "停止服务", en: "Stop Service" },
  "serviceDetail.startTitle": { zh: "启动服务", en: "Start Service" },
  "serviceDetail.startDesc": { zh: "确定要激活 {name} 吗？这将启用流量路由并按需扩展资源。", en: "Are you sure you want to activate {name}? This will enable traffic routing and scale up resources on demand." },
  "serviceDetail.startConfirm": { zh: "启动服务", en: "Start Service" },

  // ===== Job Form Help =====
  "help.jobForm.intro": {
    zh: "通过此表单向 Magnus 调度系统提交计算任务。任务将根据优先级和集群资源可用性自动调度执行。",
    en: "Submit compute tasks to the Magnus scheduling system via this form. Tasks will be automatically scheduled based on priority and cluster resource availability."
  },
  "help.jobForm.requiredFields": { zh: "必填字段", en: "Required Fields" },
  "help.jobForm.taskName": {
    zh: "任务名称，用于在任务列表中标识和搜索。建议使用有意义的命名，如 train-resnet50-epoch100。",
    en: "Task name, used to identify and search in the task list. Use meaningful names like train-resnet50-epoch100."
  },
  "help.jobForm.namespace": {
    zh: "GitHub 仓库的组织名和仓库名。系统会通过 SSH 拉取代码，确保仓库已配置正确的访问权限。",
    en: "GitHub organization and repository name. The system pulls code via SSH, ensure the repository has correct access permissions."
  },
  "help.jobForm.branch": {
    zh: "点击 Scan Repository 后可选择分支和具体提交。HEAD 表示使用该分支的最新提交。",
    en: "Click Scan Repository to select a branch and specific commit. HEAD means using the latest commit of that branch."
  },
  "help.jobForm.entryCommand": {
    zh: "任务启动命令，支持多行。每行作为独立命令顺序执行，工作目录为仓库根目录。",
    en: "Task start command, supports multiple lines. Each line executes as an independent command sequentially, working directory is the repository root."
  },
  "help.jobForm.priority": { zh: "任务优先级", en: "Task Priority" },
  "help.jobForm.priorityIntro": {
    zh: "Magnus 采用四级优先级调度，高优先级任务可抢占低优先级任务的资源：",
    en: "Magnus uses four-level priority scheduling, higher priority tasks can preempt resources from lower priority tasks:"
  },
  "help.jobForm.a1": {
    zh: "最高优先级，适用于紧急任务。不可被抢占，立即获得资源。",
    en: "Highest priority, for urgent tasks. Non-preemptible, gets resources immediately."
  },
  "help.jobForm.a2": {
    zh: "高优先级，日常生产任务的默认选择。不可被抢占。",
    en: "High priority, the default choice for daily production tasks. Non-preemptible."
  },
  "help.jobForm.b1": {
    zh: "标准优先级，适用于非紧急的开发和测试。可被 A 类任务抢占，抢占后状态变为 Paused。",
    en: "Standard priority, for non-urgent development and testing. Can be preempted by A-class tasks, status changes to Paused."
  },
  "help.jobForm.b2": {
    zh: "低优先级，适用于后台批量任务。可被 A 类任务抢占。",
    en: "Low priority, for background batch tasks. Can be preempted by A-class tasks."
  },
  "help.jobForm.resources": { zh: "计算资源配置", en: "Compute Resource Configuration" },
  "help.jobForm.gpuAccelerator": {
    zh: "选择 GPU 类型。选择 CPU Only 时 GPU Count 自动设为 0。",
    en: "Select GPU type. When CPU Only is selected, GPU Count is automatically set to 0."
  },
  "help.jobForm.gpuCount": {
    zh: "请求的 GPU 数量。多卡任务会分配到同一节点的连续 GPU。",
    en: "Number of GPUs requested. Multi-GPU tasks are allocated to consecutive GPUs on the same node."
  },
  "help.jobForm.advancedOptions": {
    zh: "展开可配置 CPU 核心数、内存大小、临时存储大小、指定运行用户等高级选项。",
    en: "Expand to configure advanced options like CPU cores, memory size, ephemeral storage size, and specified runner user."
  },
  "help.jobForm.configReuse": { zh: "配置复用", en: "Configuration Reuse" },
  "help.jobForm.configReuseDesc": {
    zh: "右上角提供配置的导出和导入功能。点击复制按钮可将当前配置导出为 JSON，点击粘贴按钮可从剪贴板导入之前保存的配置。支持跨浏览器、跨设备复用配置。",
    en: "Export and import configuration options are available in the top right. Click the copy button to export current configuration as JSON, click paste to import previously saved configuration. Supports cross-browser and cross-device configuration reuse."
  },
  "help.jobForm.runtimeEnv": { zh: "运行时环境", en: "Runtime Environment" },
  "help.jobForm.runtimeEnvDesc": {
    zh: "任务在 Apptainer 容器中以 --containall 模式隔离运行。宿主机环境变量、/tmp、home 目录均不会泄露到容器内。目录结构如下：",
    en: "Tasks run in isolated Apptainer containers with --containall. Host environment variables, /tmp, and home directory are not leaked into the container. Directory structure:"
  },
  "help.jobForm.envToken": {
    zh: "用于 SDK 认证的 JWT 令牌",
    en: "JWT token for SDK authentication"
  },
  "help.jobForm.envAddress": {
    zh: "Magnus 服务地址，SDK 回调用",
    en: "Magnus server address for SDK callbacks"
  },
  "help.jobForm.envJobId": {
    zh: "当前任务 ID",
    en: "Current job ID"
  },
  "help.jobForm.envResult": {
    zh: "结果文件路径，任务结束后由调度器读取",
    en: "Result file path, read by scheduler after task completion"
  },
  "help.jobForm.envAction": {
    zh: "Action 文件路径，用于任务后续动作",
    en: "Action file path, for post-task actions"
  },
  "help.jobForm.runtimeWritability": {
    zh: "workspace/ 下的写入会持久化到宿主机。容器其余路径通过 Ephemeral Storage（临时存储）提供可写层，任务结束后自动销毁。",
    en: "Writes under workspace/ persist to the host. Other container paths are writable via Ephemeral Storage overlay, automatically destroyed after task completion."
  },
  "help.jobForm.networkIsolation": { zh: "网络与代理", en: "Network & Proxy" },
  "help.jobForm.networkIsolationDesc": {
    zh: "默认使用宿主机网络（host 模式）。可通过环境变量 MAGNUS_NET_MODE=bridge 切换为 rootlesskit 桥接模式，容器获得独立网络命名空间，通过 MAGNUS_PORT_MAP 声明端口映射。",
    en: "Host network by default. Set MAGNUS_NET_MODE=bridge for rootlesskit bridge mode with isolated network namespace. Use MAGNUS_PORT_MAP for port forwarding."
  },
  "help.jobForm.proxyPassthrough": {
    zh: "宿主机的代理环境变量（HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 等）会自动传入容器。桥接模式下 127.0.0.1 自动转译为网关地址（默认 10.0.2.2）。",
    en: "Host proxy env vars (HTTP_PROXY / HTTPS_PROXY / ALL_PROXY, etc.) are auto-forwarded into the container. In bridge mode, 127.0.0.1 is auto-translated to the gateway address (default 10.0.2.2)."
  },

  // ===== Service Form Help =====
  "help.serviceForm.intro": {
    zh: "弹性服务 (Elastic Service) 是独立于任务调度器的长期运行服务单元。服务会根据流量自动启停，空闲时释放资源，有请求时自动唤醒，实现按需伸缩。",
    en: "Elastic Service is a long-running service unit independent of the task scheduler. Services automatically start/stop based on traffic, release resources when idle, and auto-wake on request, enabling on-demand scaling."
  },
  "help.serviceForm.identity": { zh: "服务标识", en: "Service Identity" },
  "help.serviceForm.serviceName": {
    zh: "服务的显示名称，用于在服务列表中识别。",
    en: "Display name of the service, used for identification in the service list."
  },
  "help.serviceForm.serviceId": {
    zh: "服务的唯一标识符，用于 API 调用和 CLI 访问。必须是 URL 安全的小写字符串，如 llm-inference、image-gen-v2。创建后不可修改。",
    en: "Unique identifier for the service, used for API calls and CLI access. Must be a URL-safe lowercase string, e.g., llm-inference, image-gen-v2. Cannot be modified after creation."
  },
  "help.serviceForm.lifecycle": { zh: "生命周期配置", en: "Lifecycle Configuration" },
  "help.serviceForm.idleTimeout": {
    zh: "服务空闲多长时间后自动停止。设置较短的超时可节省资源，但会增加冷启动频率。建议根据服务的启动时间和使用频率权衡设置。",
    en: "How long the service idles before auto-stopping. Shorter timeouts save resources but increase cold start frequency. Set based on service startup time and usage frequency."
  },
  "help.serviceForm.requestTimeout": {
    zh: "单次请求的最大等待时间。超时后请求会返回错误。对于耗时较长的推理任务，应适当增大此值。",
    en: "Maximum wait time for a single request. Returns error on timeout. For longer inference tasks, increase this value appropriately."
  },
  "help.serviceForm.maxConcurrency": {
    zh: "服务同时处理的最大请求数。超出并发限制的请求会排队等待。",
    en: "Maximum number of concurrent requests the service handles. Requests exceeding the limit will queue."
  },
  "help.serviceForm.lifecycleFlow": { zh: "服务生命周期", en: "Service Lifecycle" },
  "help.serviceForm.flow1": {
    zh: "创建服务后，系统自动分配一个固定的端口号 (MAGNUS_PORT)",
    en: "After creating the service, the system automatically assigns a fixed port number (MAGNUS_PORT)"
  },
  "help.serviceForm.flow2": {
    zh: "首次收到请求时，系统启动底层 SLURM 任务运行服务代码",
    en: "On first request, the system starts the underlying SLURM task to run service code"
  },
  "help.serviceForm.flow3": {
    zh: "服务启动后，后续请求直接转发到运行中的实例",
    en: "After startup, subsequent requests are forwarded directly to the running instance"
  },
  "help.serviceForm.flow4": {
    zh: "持续无请求达到 Idle Timeout 后，系统自动终止底层任务释放资源",
    en: "After no requests for the Idle Timeout period, the system auto-terminates the underlying task to release resources"
  },
  "help.serviceForm.flow5": {
    zh: "再次收到请求时，系统重新启动服务（冷启动）",
    en: "On receiving requests again, the system restarts the service (cold start)"
  },
  "help.serviceForm.accessMethods": { zh: "访问方式", en: "Access Methods" },

  // ===== Blueprint Editor Help =====
  "help.blueprintEditor.intro": {
    zh: "蓝图 (Blueprint) 实现了「Python 函数即前端表单」的开发模式。编写一个带类型注解的 Python 函数，系统自动解析函数签名生成对应的前端表单界面，用户填写参数后调用函数生成任务配置并提交执行。",
    en: "Blueprint implements the 'Python function as frontend form' development pattern. Write a type-annotated Python function, the system auto-parses the function signature to generate the corresponding frontend form interface, users fill in parameters to call the function, generate task config and submit for execution."
  },
  "help.blueprintEditor.runtime": { zh: "运行环境", en: "Runtime Environment" },
  "help.blueprintEditor.runtimeDesc": {
    zh: "蓝图代码在执行时，以下符号已自动导入，无需手动 import：",
    en: "When executing blueprint code, the following symbols are auto-imported, no manual import needed:"
  },
  "help.blueprintEditor.autoImported": { zh: "已自动导入", en: "auto-imported" },
  "help.blueprintEditor.functionSpec": { zh: "函数规范", en: "Function Specification" },
  "help.blueprintEditor.functionSpecDesc": {
    zh: "蓝图代码必须定义一个名为 blueprint 的函数，在函数体内调用 submit_job 提交任务：",
    en: "Blueprint code must define a function named blueprint that calls submit_job to submit a job:"
  },
  "help.blueprintEditor.commitSha": { zh: "commit_sha 智能解析", en: "commit_sha Smart Resolution" },
  "help.blueprintEditor.commitShaModes": {
    zh: "commit_sha 支持三种模式：",
    en: "commit_sha supports three modes:",
  },
  "help.blueprintEditor.commitShaHead": {
    zh: "使用指定分支的最新提交",
    en: "Latest commit on the specified branch",
  },
  "help.blueprintEditor.commitShaExact": {
    zh: "精确的 40 位 SHA 或任何有效的 git ref",
    en: "Exact 40-char SHA or any valid git ref",
  },
  "help.blueprintEditor.commitShaMsg": {
    zh: "搜索最近的 commit message 匹配该 Python 正则表达式的提交",
    en: "Search for the most recent commit whose message matches the Python regex",
  },
  "help.blueprintEditor.commitShaNote": {
    zh: "解析后的真实 SHA 会回写到数据库——原始的 msg: 值不会被永久存储。若最近 200 条 commit 中没有匹配项，任务将失败并返回描述性错误信息。",
    en: "The resolved SHA is written back to the database — the original msg: value is never stored permanently. If no match is found within the most recent 200 commits, the job will fail with a descriptive error.",
  },
  "help.blueprintEditor.commitShaEx1": { zh: "分支最新提交", en: "latest on branch" },
  "help.blueprintEditor.commitShaEx2": { zh: "正则匹配 commit message", en: "most recent commit matching regex" },
  "help.blueprintEditor.commitShaEx3": { zh: "包含 \"release\" 的提交", en: "commit message containing \"release\"" },
  "help.blueprintEditor.commitShaEx4": { zh: "转义点号", en: "literal dot (regex escaped)" },
  "help.blueprintEditor.functionSpecCode": {
    zh: `# 定义参数类型（带元数据）
MyParam = Annotated[str, {
    "label": "参数名称",
    "description": "参数说明",
    "placeholder": "输入提示",
}]

def blueprint(
    required_param: MyParam,
    optional_param: Optional[str] = None,
):
    submit_job(
        task_name="my-task",           # 任务名称
        entry_command="python main.py",# 启动命令
        repo_name="my-repo",           # 仓库名
        description=None,              # 任务描述 (可选)
        namespace="Rise-AGI",          # GitHub 组织名
        branch=None,                   # 分支名 (None=自动检测)
        commit_sha=None,               # 提交 SHA (None=HEAD)
        gpu_type="cpu",                # GPU 类型
        gpu_count=0,                   # GPU 数量
        job_type=JobType.A2,           # 优先级
    )`,
    en: `# Define parameter types (with metadata)
MyParam = Annotated[str, {
    "label": "Parameter Name",
    "description": "Parameter description",
    "placeholder": "Input hint",
}]

def blueprint(
    required_param: MyParam,
    optional_param: Optional[str] = None,
):
    submit_job(
        task_name="my-task",           # task name
        entry_command="python main.py",# entry command
        repo_name="my-repo",           # repository name
        description=None,              # description (optional)
        namespace="Rise-AGI",          # GitHub org
        branch=None,                   # branch (None=auto-detect)
        commit_sha=None,               # SHA (None=HEAD)
        gpu_type="cpu",                # GPU type
        gpu_count=0,                   # GPU count
        job_type=JobType.A2,           # priority
    )`,
  },
  "help.blueprintEditor.paramTypes": { zh: "支持的参数类型", en: "Supported Parameter Types" },
  "help.blueprintEditor.pythonType": { zh: "Python 类型", en: "Python Type" },
  "help.blueprintEditor.formControl": { zh: "表单控件", en: "Form Control" },
  "help.blueprintEditor.metadata": { zh: "可用元数据", en: "Available Metadata" },
  "help.blueprintEditor.textInput": { zh: "文本输入框", en: "Text Input" },
  "help.blueprintEditor.intStepper": { zh: "整数步进器", en: "Integer Stepper" },
  "help.blueprintEditor.floatInput": { zh: "浮点数输入框", en: "Float Input" },
  "help.blueprintEditor.switch": { zh: "开关", en: "Switch" },
  "help.blueprintEditor.dropdown": { zh: "下拉选择器", en: "Dropdown Selector" },
  "help.blueprintEditor.optionsDesc": {
    zh: "options (可为每个选项定义 label 和 description)",
    en: "options (can define label and description for each option)"
  },
  "help.blueprintEditor.optionalField": { zh: "带启用开关的字段", en: "Field with Enable Switch" },
  "help.blueprintEditor.optionalDesc": { zh: "禁用时不传参给函数", en: "Disabled means no param passed to function" },
  "help.blueprintEditor.fileSecretInput": { zh: "文件凭证输入框", en: "File secret input" },
  "help.blueprintEditor.dynamicList": { zh: "可动态增删的列表", en: "Dynamic Add/Remove List" },
  "help.blueprintEditor.nestedTypes": { zh: "支持嵌套基础类型", en: "Supports nested basic types" },
  "help.blueprintEditor.commonMetadata": { zh: "通用元数据属性", en: "Common Metadata Properties" },
  "help.blueprintEditor.labelDesc": { zh: "字段在表单中显示的名称", en: "Field display name in the form" },
  "help.blueprintEditor.descriptionDesc": { zh: "字段的详细说明，显示在输入框下方", en: "Detailed field description, shown below the input" },
  "help.blueprintEditor.scopeDesc": { zh: "参数分组，相同 scope 的参数会被归类到同一区域显示", en: "Parameter grouping, params with the same scope are grouped together" },
  "help.blueprintEditor.shortcuts": { zh: "代码编辑器快捷键", en: "Code Editor Shortcuts" },
  "help.blueprintEditor.tabDesc": {
    zh: "插入 4 个空格的缩进。选中多行时，为所有选中行添加缩进。",
    en: "Insert 4 spaces for indentation. When multiple lines are selected, add indentation to all selected lines."
  },
  "help.blueprintEditor.shiftTabDesc": {
    zh: "减少缩进。删除行首最多 4 个空格。选中多行时，为所有选中行减少缩进。",
    en: "Reduce indentation. Remove up to 4 spaces from line start. When multiple lines are selected, reduce indentation for all."
  },
  "help.blueprintEditor.commentDesc": {
    zh: "注释或取消注释。自动判断当前行或选中行的状态，智能切换。保留原有缩进。",
    en: "Comment or uncomment. Auto-detects current/selected line state, smart toggle. Preserves original indentation."
  },
  "help.blueprintEditor.paramCache": { zh: "参数缓存", en: "Parameter Cache" },
  "help.blueprintEditor.paramCacheDesc": {
    zh: "通过 Web 界面成功运行蓝图后，系统会自动保存用户填写的参数值。下次打开同一蓝图时，如果蓝图签名未发生变化，会自动恢复上次的参数值，无需重复填写。当蓝图代码修改导致参数签名变化时，缓存会自动失效。",
    en: "After successfully running a blueprint via the web interface, the system auto-saves user-filled parameter values. Next time opening the same blueprint, if the signature hasn't changed, previous values auto-restore. Cache invalidates when blueprint code changes alter the parameter signature."
  },
  "help.blueprintEditor.autoSave": { zh: "自动保存", en: "auto-save" },
  "help.blueprintEditor.autoRestore": { zh: "自动恢复", en: "auto-restore" },
  "help.blueprintEditor.sdkCall": { zh: "SDK 调用", en: "SDK Call" },
  "help.blueprintEditor.sdkCallDesc": {
    zh: "蓝图也可以通过 Python SDK 或 CLI 直接调用：",
    en: "Blueprints can also be called directly via Python SDK or CLI:"
  },

  // ===== Blueprint Runner Help =====
  "help.blueprintRunner.intro": {
    zh: "此表单根据蓝图定义的参数签名自动生成。填写参数后点击 Launch，系统会调用蓝图函数生成任务配置并提交到调度系统执行。",
    en: "This form is auto-generated based on the blueprint's parameter signature. After filling parameters, click Launch, the system calls the blueprint function to generate task config and submit to the scheduling system."
  },
  "help.blueprintRunner.fieldNotes": { zh: "字段说明", en: "Field Notes" },
  "help.blueprintRunner.required": {
    zh: "标记的字段为必填项，提交前必须填写有效值。",
    en: "marked fields are required, must fill valid values before submission."
  },
  "help.blueprintRunner.optional": {
    zh: "带开关的字段为可选参数 (Optional)。开关关闭时，该参数不会传递给蓝图函数，函数将使用默认值。开关开启后必须填写有效值。",
    en: "Fields with switches are optional parameters (Optional). When switch is off, the parameter won't be passed to the blueprint function, function uses default value. When on, must fill valid value."
  },
  "help.blueprintRunner.numberRange": {
    zh: "数字类型字段可能设置了最小值和最大值限制，输入超出范围的值会提示错误。",
    en: "Number fields may have min/max limits, entering out-of-range values shows an error."
  },
  "help.blueprintRunner.dropdown": {
    zh: "下拉选择字段的选项由蓝图定义，部分选项可能带有说明文字。",
    en: "Dropdown options are defined by the blueprint, some options may have description text."
  },
  "help.blueprintRunner.fileSecret": {
    zh: "文件传输字段。在此输入 secret code，前缀 magnus-secret: 已预填。SDK 用户可直接传本地文件路径。",
    en: "File transfer field. Enter the secret code, the magnus-secret: prefix is pre-filled. SDK users can pass a local file path directly."
  },
  "help.blueprintRunner.paramCache": { zh: "参数缓存", en: "Parameter Cache" },
  "help.blueprintRunner.paramCacheDesc": {
    zh: "成功提交任务后，系统会自动保存当前填写的参数值。下次打开同一蓝图时，如果蓝图签名未发生变化，会自动恢复上次的参数值，无需重复填写。当蓝图代码修改导致参数签名变化时，缓存会自动失效。",
    en: "After successful submission, the system auto-saves current parameter values. Next time opening the same blueprint, if signature hasn't changed, previous values auto-restore. Cache invalidates when blueprint code changes alter the signature."
  },
  "help.blueprintRunner.sdkCall": { zh: "SDK 调用", en: "SDK Call" },
  "help.blueprintRunner.sdkCallDesc": {
    zh: "除了通过 Web 界面运行蓝图，也可以通过 Python SDK 或 CLI 调用：",
    en: "Besides running blueprints via web interface, you can also call via Python SDK or CLI:"
  },
  "help.blueprintRunner.submitFlow": { zh: "提交流程", en: "Submission Flow" },
  "help.blueprintRunner.flow1": {
    zh: "填写所有必填参数，确保可选参数的开关状态正确",
    en: "Fill all required parameters, ensure optional parameter switches are set correctly"
  },
  "help.blueprintRunner.flow2": { zh: "点击 Launch 按钮提交", en: "Click Launch button to submit" },
  "help.blueprintRunner.flow3": {
    zh: "系统验证参数合法性，调用蓝图函数执行 submit_job",
    en: "System validates parameters, calls blueprint function to execute submit_job"
  },
  "help.blueprintRunner.flow4": {
    zh: "任务提交成功后自动跳转到任务页面",
    en: "Auto-redirects to Jobs page after successful submission"
  },
  "help.blueprintRunner.flow5": {
    zh: "在任务页面可查看任务状态、日志和结果",
    en: "View task status, logs, and results on the Jobs page"
  },

  // ===== Skill Editor Help =====
  "help.skillEditor.intro": { zh: "技能是可复用的领域知识模块，由描述文件和代码文件组成。", en: "Skills are reusable domain knowledge modules composed of description and code files." },
  "help.skillEditor.structure": { zh: "文件结构", en: "File Structure" },
  "help.skillEditor.skillMd": { zh: "必需。描述技能的用途和使用场景。", en: "Required. Describes the skill's purpose and usage." },
  "help.skillEditor.additionalFiles": { zh: "附加文件", en: "Additional files" },
  "help.skillEditor.additionalFilesDesc": { zh: "代码、配置等支撑文件，支持语法高亮。", en: "Code, configs, and other supporting files with syntax highlighting." },
  "help.skillEditor.shortcuts": { zh: "快捷键", en: "Keyboard Shortcuts" },
  "help.skillEditor.saveShortcut": { zh: "保存但不关闭编辑器", en: "Save without closing the editor" },
  "help.skillEditor.tabShortcut": { zh: "增加 / 减少缩进（4空格）", en: "Indent / Dedent (4 spaces)" },
  "help.skillEditor.commentShortcut": { zh: "切换注释", en: "Toggle comment" },

  "skillEditor.help": { zh: "技能编辑器帮助", en: "Skill Editor Help" },

  // ===== Skills =====
  "skills.subtitle": { zh: "领域知识模块", en: "Domain knowledge modules for AI agents" },
  "skills.new": { zh: "新建技能", en: "New Skill" },
  "skills.searchPlaceholder": { zh: "搜索技能...", en: "Search skills..." },
  "skills.filterByUser": { zh: "按用户筛选", en: "Filter by user" },
  "skills.fetching": { zh: "正在加载技能...", en: "Loading skills..." },
  "skills.noFound": { zh: "暂无技能", en: "No skills found" },
  "skills.clone": { zh: "克隆", en: "Clone" },
  "skills.deleteTitle": { zh: "删除技能", en: "Delete Skill" },
  "skills.deleteConfirm": {
    zh: "确定要删除技能「{title}」吗？此操作不可撤销。",
    en: "Are you sure you want to delete skill \"{title}\"? This action cannot be undone."
  },
  "skills.table.skill": { zh: "技能", en: "Skill" },
  "skills.table.description": { zh: "描述", en: "Description" },
  "skills.table.author": { zh: "作者", en: "Author" },
  "skills.table.files": { zh: "文件", en: "Files" },

  // Skills Detail
  "skillDetail.backTo": { zh: "返回技能", en: "Back to Skills" },
  "skillDetail.notFound": { zh: "技能不存在", en: "Skill Not Found" },
  "skillDetail.notFoundDesc": {
    zh: "找不到 ID 为「{id}」的技能。",
    en: "Could not find a skill with ID \"{id}\"."
  },
  "skillDetail.returnToRegistry": { zh: "返回技能列表", en: "Return to Skills" },
  "skillDetail.author": { zh: "作者", en: "AUTHOR" },
  "skillDetail.description": { zh: "描述", en: "Description" },
  "skillDetail.files": { zh: "文件", en: "Files" },
  "skillDetail.noFiles": { zh: "暂无文件", en: "No files" },
  "skillDetail.noDescription": { zh: "暂无描述", en: "No description" },
  "skillDetail.deleteSkill": { zh: "删除技能", en: "Delete Skill" },
  "skillDetail.deleteConfirmDesc": {
    zh: "此操作将永久删除技能「{title}」及其所有文件。",
    en: "This will permanently delete skill \"{title}\" and all its files."
  },
  "skillDetail.editClone": { zh: "编辑 / 克隆", en: "Edit / Clone" },

  // Skills Editor
  "skillEditor.create": { zh: "新建技能", en: "Create Skill" },
  "skillEditor.cloneUpdate": { zh: "编辑 / 克隆技能", en: "Edit / Clone Skill" },
  "skillEditor.basicInfo": { zh: "基本信息", en: "Basic Info" },
  "skillEditor.name": { zh: "名称", en: "Name" },
  "skillEditor.nameRequired": { zh: "名称不能为空", en: "Name is required" },
  "skillEditor.id": { zh: "ID", en: "ID" },
  "skillEditor.idRequired": { zh: "ID 不能为空", en: "ID is required" },
  "skillEditor.idHint": { zh: "唯一标识符，创建后慎改", en: "Unique identifier, be careful changing after creation" },
  "skillEditor.descriptionRequired": { zh: "描述不能为空", en: "Description is required" },
  "skillEditor.filesSection": { zh: "文件", en: "Files" },
  "skillEditor.addFile": { zh: "添加文件", en: "Add File" },
  "skillEditor.fileName": { zh: "文件名", en: "Filename" },
  "skillEditor.fileContent": { zh: "内容", en: "Content" },
  "skillEditor.skillMdRequired": { zh: "SKILL.md 为必选文件", en: "SKILL.md is required" },
  "skillEditor.saved": { zh: "已保存", en: "Saved" },
  "skillEditor.updating": { zh: "将更新现有技能", en: "Will update existing skill" },
  "skillEditor.creating": { zh: "将创建新技能", en: "Will create new skill" },
  "skillEditor.updateBtn": { zh: "更新", en: "Update" },
  "skillEditor.createBtn": { zh: "创建", en: "Create" },
  "skillEditor.cloneBtn": { zh: "克隆创建", en: "Clone" },
  "skillEditor.filePathEmpty": { zh: "文件路径不能为空", en: "File path cannot be empty" },
  "skillEditor.filePathUntitled": { zh: "(未命名)", en: "(untitled)" },
  "skillEditor.filePathDuplicate": { zh: "文件路径重复: {v}", en: "Duplicate file path: {v}" },

  // ===== Editor Common =====
  "editor.unsavedChanges": { zh: "有未保存的更改，确定要关闭吗？", en: "Discard unsaved changes?" },
  "editor.saveFailed": { zh: "保存失败", en: "Save failed" },

  // ===== Images =====
  "images.subtitle": { zh: "集群容器镜像缓存", en: "Cluster container image cache" },
  "images.preheat": { zh: "预热镜像", en: "Preheat Image" },
  "images.searchPlaceholder": { zh: "搜索镜像...", en: "Search images..." },
  "images.fetching": { zh: "正在加载...", en: "Loading..." },
  "images.noFound": { zh: "暂无缓存镜像", en: "No cached images" },
  "images.refresh": { zh: "刷新镜像", en: "Refresh Image" },
  "images.preheatTitle": { zh: "预热镜像", en: "Preheat Image" },
  "images.detailTitle": { zh: "镜像详情", en: "Image Details" },
  "images.detail.created": { zh: "创建时间", en: "Created" },
  "images.detail.updated": { zh: "更新时间", en: "Updated" },
  "images.preheating": { zh: "预热中...", en: "Preheating..." },
  "images.deleteTitle": { zh: "删除镜像", en: "Delete Image" },
  "images.deleteConfirm": {
    zh: "确定要删除「{uri}」吗？SIF 文件和数据库记录都将被删除。",
    en: "Delete \"{uri}\"? Both the SIF file and database record will be removed."
  },
  "images.uri": { zh: "镜像 URI", en: "Image URI" },
  "images.uriRequired": { zh: "URI 不能为空", en: "URI is required" },
  "images.table.owner": { zh: "所有者", en: "Owner" },
  "images.table.size": { zh: "大小", en: "Size" },
  "images.table.status": { zh: "状态", en: "Status" },
  "images.status.cached": { zh: "已就绪", en: "Cached" },
  "images.status.pulling": { zh: "拉取中", en: "Pulling" },
  "images.status.refreshing": { zh: "刷新中", en: "Refreshing" },
  "images.status.unregistered": { zh: "未注册", en: "Unregistered" },
  "images.status.missing": { zh: "文件缺失", en: "Missing" },

  // ===== People =====
  "people.subtitle": { zh: "团队成员", en: "Team members" },
  "people.recruitTitle": { zh: "招募人员", en: "Recruit Member" },
  "people.fetching": { zh: "正在加载...", en: "Loading..." },
  "people.noFound": { zh: "暂无成员", en: "No members found" },
  "people.searchPlaceholder": { zh: "搜索成员...", en: "Search members..." },
  "people.table.member": { zh: "成员", en: "Member" },
  "people.table.leader": { zh: "直属上级", en: "+1 Leader" },
  "people.table.bpSvc": { zh: "蓝图 / 服务", en: "Blueprints / Services" },
  "people.table.headcount": { zh: "属额", en: "Headcount" },
  "people.role.admin": { zh: "管理员", en: "Admin" },
  "people.leader.void": { zh: "无", en: "void" },
  "people.drawer.title": { zh: "成员详情", en: "Member Details" },
  "people.drawer.headcount": { zh: "属额", en: "Headcount" },
  "people.drawer.created": { zh: "加入时间", en: "Joined" },
  "people.drawer.delete": { zh: "删除", en: "Delete" },
  "people.drawer.deleteConfirm": { zh: "确定删除此成员？此操作不可撤销。", en: "Delete this member? This cannot be undone." },
  "people.drawer.editHeadcount": { zh: "点击编辑属额", en: "Click to edit headcount" },
  "people.drawer.avatarHint": { zh: "点击上传头像", en: "Click to upload avatar" },
  "people.recruit.name": { zh: "名称", en: "Name" },
  "people.recruit.nameRequired": { zh: "请输入成员名称", en: "Name is required" },
  "people.recruit.namePlaceholder": { zh: "输入成员名称", en: "Enter member name" },
  "people.recruit.submit": { zh: "招募", en: "Recruit" },
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

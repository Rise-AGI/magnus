// front_end/src/hooks/use-job-operations.tsx
import { useState } from "react";
import { Job } from "@/types/job";
import { JobFormData } from "@/components/jobs/job-form";
import { client } from "@/lib/api";
import { useLanguage } from "@/context/language-context";

interface UseJobOperationsProps {
  onSuccess?: () => void;
  onTerminateSuccess?: () => void;
}

export function useJobOperations({ onSuccess, onTerminateSuccess }: UseJobOperationsProps = {}) {
  const { t } = useLanguage();
  // --- Drawer / Form State ---
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [drawerMode, setDrawerMode] = useState<"create" | "clone">("create");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [cloneData, setCloneData] = useState<JobFormData | null>(null);
  // 用于强制刷新 Form (主要用于详情页连续克隆场景)
  const [formKey, setFormKey] = useState(0);

  // --- Terminate Dialog State ---
  const [jobToTerminate, setJobToTerminate] = useState<Job | null>(null);
  const [isTerminating, setIsTerminating] = useState(false);

  // --- Signal Dialog State ---
  const [jobToSignal, setJobToSignal] = useState<Job | null>(null);
  const [isSignaling, setIsSignaling] = useState(false);

  // --- Error Dialog State ---
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // 打开新建窗口
  const handleNewJob = () => {
    setDrawerMode("create");
    setCloneData(null);
    setSelectedJobId(null);
    setFormKey((k) => k + 1);
    setIsDrawerOpen(true);
  };

  // 打开克隆窗口。列表 / 看板给的是轻量投影（后端 JobListItem），不含 entry_command /
  // system_entry_command（可能高达几十 MB）。点击克隆时按需拉一次完整 job 再回填表单，
  // 保证克隆保真；正常 job 这一步近乎无感，只有内联超大命令的 job 才会真正搬运其体量。
  const handleCloneJob = async (job: Job) => {
    setDrawerMode("clone");
    setSelectedJobId(job.id);
    let full: Job = job;
    try {
      full = await client(`/api/jobs/${job.id}`);
    } catch (e) {
      console.error("Failed to load job detail for clone", e);
    }
    setCloneData({
      taskName: full.task_name, // 克隆时往往需要修改名字，这里保持原名或让用户自己改
      description: full.description || "",
      namespace: full.namespace,
      repoName: full.repo_name,
      branch: full.branch || "",
      commit_sha: full.commit_sha || "",
      entry_command: full.entry_command ?? "",
      gpu_count: full.gpu_count,
      gpu_type: full.gpu_type,
      job_type: full.job_type,
      cpu_count: full.cpu_count,
      memory_demand: full.memory_demand,
      time_limit: full.time_limit,
      ephemeral_storage: full.ephemeral_storage,
      runner: full.runner,
      container_image: full.container_image,
      system_entry_command: full.system_entry_command,
    });
    setFormKey((k) => k + 1);
    setIsDrawerOpen(true);
  };

  // 打开终止确认弹窗
  const onClickTerminate = (job: Job) => {
    setJobToTerminate(job);
  };

  // 执行终止 API
  const executeTermination = async () => {
    if (!jobToTerminate) return;
    setIsTerminating(true);
    try {
      await client(`/api/jobs/${jobToTerminate.id}/terminate`, { method: "POST" });
      if (onTerminateSuccess) {
        onTerminateSuccess();
      } else if (onSuccess) {
        onSuccess();
      }
      setJobToTerminate(null);
    } catch (e) {
      setErrorMessage(t("jobOps.terminateFailed"));
      console.error(e);
    } finally {
      setIsTerminating(false);
    }
  };

  // 打开发送 SIGTERM 确认弹窗（对齐 terminate UX，避免无交互感的误触）
  const onClickSignal = (job: Job) => {
    setJobToSignal(job);
  };

  // 执行发送 SIGTERM API
  const executeSignal = async () => {
    if (!jobToSignal) return;
    setIsSignaling(true);
    try {
      await client(`/api/jobs/${jobToSignal.id}/signal`, { method: "POST" });
      setJobToSignal(null);
    } catch (e) {
      setErrorMessage(t("jobOps.signalFailed"));
      console.error(e);
    } finally {
      setIsSignaling(false);
    }
  };

  return {
    // Drawer 相关属性，直接传递给 JobDrawer
    drawerProps: {
      isOpen: isDrawerOpen,
      mode: drawerMode,
      initialData: cloneData,
      formKey: `${drawerMode}-${selectedJobId}-${formKey}`,
      onClose: () => setIsDrawerOpen(false),
      onSuccess: () => {
        setIsDrawerOpen(false);
        setTimeout(() => {
          const main = document.querySelector('main');
          if (main) main.scrollTo({ top: 0, behavior: 'smooth' });
        }, 350);
        if (onSuccess) onSuccess();
      },
    },
    // Dialog 相关属性，直接传递给 ConfirmationDialog
    terminateDialogProps: {
      isOpen: !!jobToTerminate,
      onClose: () => setJobToTerminate(null),
      onConfirm: executeTermination,
      isLoading: isTerminating,
      title: t("jobOps.terminateTitle"),
      description: jobToTerminate ? (
        <span>
          {t("jobOps.terminateDesc", { name: jobToTerminate.task_name })}
        </span>
      ) : null,
      confirmText: t("jobOps.terminateBtn"),
      variant: "danger" as const,
    },
    // Signal Dialog 属性，对齐 terminate UX：先确认再发送
    signalDialogProps: {
      isOpen: !!jobToSignal,
      onClose: () => setJobToSignal(null),
      onConfirm: executeSignal,
      isLoading: isSignaling,
      title: t("jobOps.signalTitle"),
      description: jobToSignal ? (
        <span>
          {t("jobOps.signalDesc", { name: jobToSignal.task_name })}
        </span>
      ) : null,
      confirmText: t("jobOps.signalBtn"),
      variant: "default" as const,
    },
    // Error Dialog 属性
    errorDialogProps: {
      isOpen: !!errorMessage,
      onClose: () => setErrorMessage(null),
      title: t("common.error"),
      description: errorMessage,
      confirmText: t("common.ok"),
      mode: "alert" as const,
      variant: "danger" as const,
    },
    // 暴露出的操作函数
    handleNewJob,
    handleCloneJob,
    onClickTerminate,
    onClickSignal,
  };
}

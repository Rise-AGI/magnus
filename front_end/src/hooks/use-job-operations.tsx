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

  // 打开克隆窗口
  const handleCloneJob = (job: Job) => {
    setDrawerMode("clone");
    setSelectedJobId(job.id);
    setCloneData({
      taskName: job.task_name, // 克隆时往往需要修改名字，这里保持原名或让用户自己改
      description: job.description || "",
      namespace: job.namespace,
      repoName: job.repo_name,
      branch: job.branch || "",
      commit_sha: job.commit_sha || "",
      entry_command: job.entry_command,
      gpu_count: job.gpu_count,
      gpu_type: job.gpu_type,
      job_type: job.job_type,
      cpu_count: job.cpu_count,
      memory_demand: job.memory_demand,
      ephemeral_storage: job.ephemeral_storage,
      runner: job.runner,
      container_image: job.container_image,
      system_entry_command: job.system_entry_command,
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
  };
}
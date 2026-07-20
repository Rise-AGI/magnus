// front_end/src/types/job.ts
import { User } from "@/types/auth";

export interface Job {
  id: string;
  task_name: string;
  description?: string;
  user?: User;
  status: string;
  namespace: string;
  repo_name: string;
  branch: string | null;
  commit_sha: string | null;
  gpu_count: number;
  gpu_type: string;
  // 列表 / 看板走轻量投影（后端 JobListItem），不含这两个可能高达几十 MB 的命令列；
  // 详情视图（GET /jobs/{id} → JobResponse）才带全。故这里是 optional，消费方按需拉详情。
  entry_command?: string;
  job_type: string;
  created_at: string;
  slurm_job_id?: string;
  cpu_count?: number | null;
  memory_demand?: string | null;
  time_limit?: number | null;
  ephemeral_storage?: string | null;
  runner?: string | null;
  container_image: string;
  system_entry_command?: string;
  result?: string;
  action?: string;
  // True ⇔ scancel 已发但 SLURM CG 还在持有资源（详见后端 schemas/_job.py
  // JobListItem.is_releasing computed field）。前端拿来直接做 UX 决策，避免
  // 重复实现 `status × slurm_job_id` 组合推断。
  is_releasing?: boolean;
}
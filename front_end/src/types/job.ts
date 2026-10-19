// front_end/src/types/job.ts
import { User } from "@/types/auth";

export interface JobResourceSummary {
  basis: "request" | "allocation";
  mode: "slurm_per_cpu" | "slurm_per_node" | "local";
  mode_inferred: boolean;
  allocation_inferred?: boolean;
  cpu_source?: string | null;
  memory_source?: string | null;
  gpu_source?: string | null;
  slurm_state?: string | null;
  cpu_count: number | null;
  memory_bytes: number | null;
  gpu_count: number;
  node_count: number | null;
  tasks_per_node: number | null;
}

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
  // 列表 / 看板走轻量投影，不包含命令和 Dockerfile 原文；
  // 详情视图（GET /jobs/{id} → JobResponse）才带全。故这里是 optional，消费方按需拉详情。
  entry_command?: string;
  job_type: string;
  created_at: string;
  slurm_job_id?: string;
  cpu_count?: number | null;
  memory_demand?: string | null;
  node_count?: number | null;
  tasks_per_node?: number | null;
  resource_summary?: JobResourceSummary | null;
  time_limit?: number | null;
  ephemeral_storage?: string | null;
  runner?: string | null;
  container_image: string | null;
  dockerfile?: string | null;
  has_dockerfile?: boolean;
  system_entry_command?: string;
  result?: string;
  action?: string;
  // True ⇔ scancel 已发但 SLURM CG 还在持有资源（详见后端 schemas/_job.py
  // JobListItem.is_releasing computed field）。前端拿来直接做 UX 决策，避免
  // 重复实现 `status × slurm_job_id` 组合推断。
  is_releasing?: boolean;
}

export interface JobCapabilities {
  dockerfile: boolean;
  image_build_enabled: boolean;
}

export interface JobBuildProgress {
  phase: "waiting" | "building" | "exporting" | "loading" | "converting" | "ready" | "failed" | "cancelled";
  elapsed_seconds: number;
  terminal: boolean;
  current_step: string | null;
  steps: {
    id: string;
    name: string;
    cached: boolean;
    completed: boolean;
    error: string | null;
  }[];
  cached_steps: number;
  completed_steps: number;
  total_steps: number;
  log_tail: string;
  error: string | null;
}

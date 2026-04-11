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
  entry_command: string;
  job_type: string;
  created_at: string;
  slurm_job_id?: string;
  cpu_count?: number | null;
  memory_demand?: string | null;
  ephemeral_storage?: string | null;
  runner?: string | null;
  container_image: string;
  system_entry_command: string;
  shared_files?: Record<string, string> | null;
  result?: string;
  action?: string;
}

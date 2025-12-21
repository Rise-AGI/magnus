// front_end/src/types/job.ts
export interface User {
  id: string;
  name: string;
  avatar_url?: string;
  email?: string;
}

export interface Job {
  id: string;
  task_name: string;
  description?: string;
  user?: User;
  status: string;
  namespace: string;
  repo_name: string;
  branch: string;
  commit_sha: string;
  gpu_count: number;
  gpu_type: string;
  entry_command: string;
  job_type: string;
  created_at: string;
  slurm_job_id?: string;
  cpu_count?: number | null;
  memory_demand?: string | null;
  runner?: string | null;
}
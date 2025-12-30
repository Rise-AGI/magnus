// front_end/src/types/service.ts
import { Job, User } from "./job";

export interface Service {
  id: string;
  owner_id: string;
  name: string;
  description?: string;
  is_active: boolean;
  last_activity_time: string;
  current_job_id?: string;
  assigned_port?: number;
  current_job?: Job;
  owner?: User;
  request_timeout: number;
  idle_timeout: number;
  max_concurrency: number;
  namespace: string;
  repo_name: string;
  branch: string;
  commit_sha: string;
  entry_command: string;
  job_task_name: string;
  job_description: string;
  gpu_count: number;
  gpu_type: string;
  job_type: string;
  cpu_count?: number | null;
  memory_demand?: string | null;
  runner?: string | null;
}
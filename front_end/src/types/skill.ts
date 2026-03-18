// front_end/src/types/skill.ts
import { User } from "@/types/auth";

export interface SkillFile {
  path: string;
  content: string;
  is_binary?: boolean;
  updated_at?: string;
}

export interface Skill {
  id: string;
  title: string;
  description: string;
  user_id: string;
  user?: User;
  files: SkillFile[];
  created_at: string;
  updated_at: string;
  can_manage?: boolean;
}

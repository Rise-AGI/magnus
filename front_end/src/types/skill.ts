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
  // 列表投影不含 files（后端 SkillListItem 省掉文件内容）；详情视图（GET /skills/{id}）才带全。
  // 故为 optional，消费方（clone）按需拉详情。
  files?: SkillFile[];
  // 列表投影用轻量标量概括文件数（详情不依赖它，直接数 files）。
  file_count?: number;
  created_at: string;
  updated_at: string;
  can_manage?: boolean;
}

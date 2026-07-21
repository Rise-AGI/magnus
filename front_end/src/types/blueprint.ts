// front_end/src/types/blueprint.ts
import { User } from "@/types/auth";

export interface Blueprint {
  id: string;
  title: string;
  description: string;
  // 列表投影不含 code（可能几十 MB，后端 BlueprintListItem 省掉了它）；详情视图
  // （GET /blueprints/{id}）才带全。故为 optional，消费方按需拉详情。
  code?: string;
  user_id: string;
  user?: User;
  updated_at: string;
  can_manage?: boolean;
}

export interface BlueprintParamOption {
  label: string;
  value: any;
  description?: string;
}

export interface BlueprintParamSchema {
  key: string;
  label: string;
  type: string;
  default?: any;
  description?: string;
  scope?: string;
  allow_empty?: boolean;
  min?: number;
  max?: number;
  placeholder?: string;
  multi_line?: boolean;
  color?: string;
  border_color?: string;
  options?: BlueprintParamOption[];
}

export interface BlueprintPreference {
  blueprint_id: string;
  blueprint_hash: string;
  cached_params: Record<string, any>;
  updated_at: string;
}
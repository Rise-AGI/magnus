// front_end/src/types/auth.ts

export interface User {
  id: string; // Hex ID
  feishu_open_id?: string | null;
  name: string;
  avatar_url?: string | null;
  email?: string | null;
  is_admin?: boolean;
}

export interface UserDetail {
  id: string;
  name: string;
  avatar_url?: string | null;
  is_admin: boolean;
  user_type: "human" | "agent";
  parent_id?: string | null;
  parent_name?: string | null;
  parent_avatar_url?: string | null;
  headcount?: number | null;       // null = ∞
  available_headcount?: number | null;
  blueprint_count: number;
  service_count: number;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: User;
}
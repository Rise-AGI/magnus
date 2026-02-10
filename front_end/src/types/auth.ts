// front_end/src/types/auth.ts

export interface User {
  id: string; // Hex ID
  feishu_open_id: string;
  name: string;
  avatar_url?: string | null;
  email?: string | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: User;
}
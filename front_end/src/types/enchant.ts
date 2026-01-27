// front_end/src/types/enchant.ts

export interface EnchantMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface EnchantSession {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface EnchantSessionWithMessages extends EnchantSession {
  messages: EnchantMessage[];
}

export interface PagedEnchantSessionResponse {
  total: number;
  items: EnchantSession[];
}

export interface Attachment {
  type: "image" | "text";
  filename: string;
  file_id?: string;
  path?: string;
  content?: string;
}

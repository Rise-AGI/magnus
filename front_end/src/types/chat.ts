export type ConversationType = "p2p" | "group";
export type MessageType = "text" | "image" | "file" | "system";

export interface ChatUserInfo {
  id: string;
  name: string;
  avatar_url?: string | null;
  email?: string | null;
}

export interface ConversationMember {
  user_id: string;
  role: string;
  last_read_at?: string | null;
  joined_at: string;
  user?: ChatUserInfo | null;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  sender_id: string;
  content: string;
  message_type: MessageType;
  created_at: string;
  sender?: ChatUserInfo | null;
}

export interface ConversationListItem {
  id: string;
  type: ConversationType;
  name?: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  member_count: number;
  last_message?: ChatMessage | null;
  other_user?: ChatUserInfo | null; // P2P 会话的对方用户
}

// 带乐观更新标记的消息（仅前端内部使用）
export interface OptimisticChatMessage extends ChatMessage {
  tempId?: string;
  isOptimistic?: boolean;
}

export interface ConversationDetail {
  id: string;
  type: ConversationType;
  name?: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  members: ConversationMember[];
}

export interface PagedConversationResponse {
  total: number;
  items: ConversationListItem[];
}

export interface PagedMessageResponse {
  total: number;
  items: ChatMessage[];
}

export interface BotCredential {
  app_id: string;
  app_secret: string;
}

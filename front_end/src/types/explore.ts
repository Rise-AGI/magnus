export interface ExplorerMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface ExplorerSession {
  id: string;
  user_id: string;
  title: string;
  is_shared: boolean;
  created_at: string;
  updated_at: string;
}

export interface ExplorerSessionWithMessages extends ExplorerSession {
  messages: ExplorerMessage[];
}

export interface PagedExplorerSessionResponse {
  total: number;
  items: ExplorerSession[];
}

export interface Attachment {
  type: "image" | "text";
  filename: string;
  file_id?: string;
  path?: string;
  content?: string;
}

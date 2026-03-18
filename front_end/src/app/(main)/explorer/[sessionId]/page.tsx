"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowUp, ArrowDown, Loader2, Pencil, ChevronDown, ChevronRight, Square, X, FileText, Image as ImageIcon, ThumbsUp, ThumbsDown, RotateCcw, Copy, Check, Paperclip } from "lucide-react";
import { client } from "@/lib/api";
import RenderMarkdown from "@/components/ui/render-markdown";
import { NotFound } from "@/components/ui/not-found";
import { VoiceInputButton } from "@/components/ui/voice-input-button";
import type { ExplorerSessionWithMessages, ExplorerMessage, Attachment } from "@/types/explore";
import { API_BASE } from "@/lib/config";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";


function parseThinkingContent(content: string): { thinking: string | null; response: string } {
  const thinkMatch = content.match(/^<think>([\s\S]*?)<\/think>\s*([\s\S]*)$/);
  if (thinkMatch) {
    return { thinking: thinkMatch[1], response: thinkMatch[2] };
  }
  return { thinking: null, response: content };
}


function ThinkingBlock({ content, defaultExpanded = true }: { content: string; defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const { t } = useLanguage();

  return (
    <div className="mb-4">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-zinc-500 hover:text-zinc-400 transition-colors mb-2"
      >
        {expanded ? (
          <ChevronDown className="w-4 h-4" />
        ) : (
          <ChevronRight className="w-4 h-4" />
        )}
        <span className="text-sm">{t("explorer.thinking")}</span>
      </button>
      {expanded && (
        <div className="pl-4 border-l-2 border-zinc-700 text-sm text-zinc-500 whitespace-pre-wrap">
          {content}
        </div>
      )}
    </div>
  );
}


function ImagePreviewModal({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <button
        onClick={onClose}
        className="absolute top-4 right-4 p-2 text-white/70 hover:text-white transition-colors"
      >
        <X className="w-6 h-6" />
      </button>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={alt}
        className="max-w-[90vw] max-h-[90vh] object-contain rounded-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      />
    </div>
  );
}


function extractFileNameFromPath(filePath: string): string | null {
  const match = filePath.match(/\/files\/([^/]+)$/);
  return match ? match[1] : null;
}


interface ParsedContent {
  type: "text" | "image" | "document";
  content?: string;
  filename?: string;
  filePath?: string;
}


function parseUserMessageContent(content: string): ParsedContent[] {
  const parts: ParsedContent[] = [];
  let lastIndex = 0;
  let match;

  const combinedPattern = /(?:\[图片: ([^\]]+)\]\(file:\/\/([^)]+)\))|(?:\n\n---\n📄 ([^\n]+)\n---\n([\s\S]*?)(?=\n\n---\n[📄🖼️]|$))/g;

  while ((match = combinedPattern.exec(content)) !== null) {
    if (match.index > lastIndex) {
      const textBefore = content.slice(lastIndex, match.index).trim();
      if (textBefore) {
        parts.push({ type: "text", content: textBefore });
      }
    }

    if (match[1] && match[2]) {
      parts.push({
        type: "image",
        filename: match[1],
        filePath: match[2],
      });
    } else if (match[3] && match[4]) {
      parts.push({
        type: "document",
        filename: match[3],
        content: match[4].trim(),
      });
    }

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < content.length) {
    const remainingText = content.slice(lastIndex).trim();
    if (remainingText) {
      parts.push({ type: "text", content: remainingText });
    }
  }

  return parts.length > 0 ? parts : [{ type: "text", content }];
}


function UserMessageContent({
  content,
  sessionId,
  onImageClick,
}: {
  content: string;
  sessionId: string;
  onImageClick: (src: string, alt: string) => void;
}) {
  const parts = parseUserMessageContent(content);
  const token = typeof window !== "undefined" ? localStorage.getItem("magnus_token") : null;

  const getImageUrl = (fileName: string | null) => {
    if (!fileName) return "";
    const baseUrl = `${API_BASE}/api/explorer/files/${sessionId}/${fileName}`;
    return token ? `${baseUrl}?token=${encodeURIComponent(token)}` : baseUrl;
  };

  return (
    <div className="space-y-2">
      {parts.map((part, index) => {
        if (part.type === "text") {
          return (
            <p key={index} className="text-sm whitespace-pre-wrap">
              {part.content}
            </p>
          );
        }

        if (part.type === "image" && part.filePath) {
          const fileName = extractFileNameFromPath(part.filePath);
          const imageUrl = getImageUrl(fileName);

          return (
            <div key={index} className="mt-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={imageUrl}
                alt={part.filename || "图片"}
                className="max-w-48 max-h-48 rounded-lg cursor-pointer hover:opacity-90 transition-opacity border border-zinc-600"
                onClick={() => onImageClick(imageUrl, part.filename || "图片")}
              />
            </div>
          );
        }

        if (part.type === "document") {
          return (
            <div
              key={index}
              className="flex items-center gap-2 bg-zinc-700/50 rounded-lg px-3 py-2 mt-2"
            >
              <FileText className="w-4 h-4 text-zinc-400 flex-shrink-0" />
              <span className="text-sm text-zinc-300 truncate">{part.filename}</span>
            </div>
          );
        }

        return null;
      })}
    </div>
  );
}


function StreamingContent({ content }: { content: string }) {
  const thinkEndIndex = content.indexOf("</think>");

  if (thinkEndIndex !== -1) {
    const thinkContent = content.slice(7, thinkEndIndex);
    const responseContent = content.slice(thinkEndIndex + 8);
    return (
      <>
        <ThinkingBlock content={thinkContent} defaultExpanded={true} />
        {responseContent && <RenderMarkdown content={responseContent} />}
      </>
    );
  }

  if (content.startsWith("<think>")) {
    const thinkContent = content.slice(7);
    return <ThinkingBlock content={thinkContent} defaultExpanded={true} />;
  }

  return <RenderMarkdown content={content} />;
}


function MessageContent({ content }: { content: string }) {
  const { thinking, response } = parseThinkingContent(content);

  return (
    <>
      {thinking && <ThinkingBlock content={thinking} />}
      {response && <RenderMarkdown content={response} />}
    </>
  );
}


function MessageActions({ content, onRegenerate, alwaysShow = false }: { content: string; onRegenerate?: () => void; alwaysShow?: boolean }) {
  const [liked, setLiked] = useState<boolean | null>(null);
  const [copied, setCopied] = useState(false);
  const { t } = useLanguage();

  const handleCopy = async () => {
    const { response } = parseThinkingContent(content);
    await navigator.clipboard.writeText(response);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={`flex items-center gap-1 mt-2 ml-9 ${alwaysShow ? "" : "opacity-100 md:opacity-0 md:group-hover:opacity-100"} transition-opacity`}>
      <button
        onClick={() => setLiked(liked === true ? null : true)}
        className={`p-2 rounded-md transition-colors ${
          liked === true
            ? "text-green-400 bg-green-400/10"
            : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800"
        }`}
        title={t("explorer.goodResponse")}
      >
        <ThumbsUp className="w-4 h-4" />
      </button>
      <button
        onClick={() => setLiked(liked === false ? null : false)}
        className={`p-2 rounded-md transition-colors ${
          liked === false
            ? "text-red-400 bg-red-400/10"
            : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800"
        }`}
        title={t("explorer.badResponse")}
      >
        <ThumbsDown className="w-4 h-4" />
      </button>
      <button
        onClick={onRegenerate}
        className="p-2 rounded-md text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
        title={t("explorer.regenerate")}
      >
        <RotateCcw className="w-4 h-4" />
      </button>
      <button
        onClick={handleCopy}
        className={`p-2 rounded-md transition-colors ${
          copied
            ? "text-green-400 bg-green-400/10"
            : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800"
        }`}
        title={copied ? t("action.copied") : t("action.copy")}
      >
        {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
      </button>
    </div>
  );
}


function UserMessageWithActions({
  message,
  index,
  isLastUserMessage,
  sessionId,
  user,
  onEdit,
  onImageClick,
}: {
  message: ExplorerMessage;
  index: number;
  isLastUserMessage: boolean;
  sessionId: string;
  user: { name: string; avatar_url?: string | null } | null;
  onEdit: () => void;
  onImageClick: (src: string, alt: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const { t } = useLanguage();

  const handleCopy = async () => {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="group flex items-start gap-2">
      <div className="opacity-100 md:opacity-0 md:group-hover:opacity-100 flex items-center gap-0.5 transition-opacity mt-2">
        {isLastUserMessage && (
          <button
            onClick={onEdit}
            className="p-1.5 hover:bg-zinc-800 rounded transition-colors"
            title={t("explorer.edit")}
          >
            <Pencil className="w-3.5 h-3.5 text-zinc-500 hover:text-zinc-300" />
          </button>
        )}
        <button
          onClick={handleCopy}
          className={`p-1.5 rounded transition-colors ${
            copied ? "text-green-400" : "hover:bg-zinc-800"
          }`}
          title={copied ? t("action.copied") : t("action.copy")}
        >
          {copied ? (
            <Check className="w-3.5 h-3.5" />
          ) : (
            <Copy className="w-3.5 h-3.5 text-zinc-500 hover:text-zinc-300" />
          )}
        </button>
      </div>
      <div className="bg-blue-600/20 border border-blue-500/30 text-zinc-100 px-4 py-3 rounded-2xl rounded-br-md">
        <UserMessageContent
          content={message.content}
          sessionId={sessionId}
          onImageClick={onImageClick}
        />
      </div>
      {user?.avatar_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={user.avatar_url}
          alt={user.name}
          className="w-7 h-7 rounded-full border border-zinc-700 object-cover flex-shrink-0 mt-1"
        />
      ) : (
        <div className="w-7 h-7 rounded-full bg-blue-600/30 text-blue-400 flex items-center justify-center text-xs font-medium border border-blue-500/30 flex-shrink-0 mt-1">
          {user?.name?.substring(0, 1) || "U"}
        </div>
      )}
    </div>
  );
}


// 标题刷新时间间隔常量
const TITLE_REFRESH_INTERVAL_MS = 2000;
const TITLE_REFRESH_INITIAL_MS = 500;
const TITLE_REFRESH_DELAYED_MS = 3000;


export default function SessionPage() {
  const params = useParams();
  const router = useRouter();
  const { user } = useAuth();
  const { t } = useLanguage();
  const sessionId = params.sessionId as string;

  const [session, setSession] = useState<ExplorerSessionWithMessages | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState("");
  const [editingMessageIndex, setEditingMessageIndex] = useState<number | null>(null);
  const [editingMessageContent, setEditingMessageContent] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [previewImage, setPreviewImage] = useState<{ src: string; alt: string } | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isUserNearBottomRef = useRef(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const pendingMessageProcessedRef = useRef(false);
  const currentUserMessageRef = useRef<ExplorerMessage | null>(null);


  useEffect(() => {
    const pendingKey = `explorer-pending-${sessionId}`;
    const pending = sessionStorage.getItem(pendingKey);
    if (pending) {
      setPendingUserMessage(pending);
    }
  }, [sessionId]);


  const fetchSession = useCallback(async () => {
    try {
      const data: ExplorerSessionWithMessages = await client(`/api/explorer/sessions/${sessionId}`);
      setSession(data);
    } catch (error) {
      console.error("Failed to fetch session:", error);
      setNotFound(true);
    }
  }, [sessionId]);


  useEffect(() => {
    fetchSession();
  }, [fetchSession]);


  const sendMessageContent = useCallback(async (messageContent: string) => {
    if (isStreaming) return;

    const userMessage: ExplorerMessage = {
      id: `temp-${Date.now()}`,
      session_id: sessionId,
      role: "user",
      content: messageContent,
      created_at: new Date().toISOString(),
    };

    currentUserMessageRef.current = userMessage;

    setSession((prev) =>
      prev ? { ...prev, messages: [...prev.messages, userMessage] } : null
    );

    setIsStreaming(true);
    setStreamingContent("");
    isUserNearBottomRef.current = true;

    abortControllerRef.current = new AbortController();
    let fullContent = "";

    const titleRefreshInterval = setInterval(() => {
      window.dispatchEvent(new Event("explorer-sessions-update"));
    }, TITLE_REFRESH_INTERVAL_MS);

    setTimeout(() => {
      window.dispatchEvent(new Event("explorer-sessions-update"));
    }, TITLE_REFRESH_INITIAL_MS);

    try {
      const token = localStorage.getItem("magnus_token");
      const response = await fetch(
        `${API_BASE}/api/explorer/sessions/${sessionId}/chat`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ content: messageContent }),
          signal: abortControllerRef.current.signal,
        }
      );

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          fullContent += chunk;
          setStreamingContent(fullContent);
        }
      }

      const assistantMessage: ExplorerMessage = {
        id: `assistant-${Date.now()}`,
        session_id: sessionId,
        role: "assistant",
        content: fullContent,
        created_at: new Date().toISOString(),
      };

      const savedUserMessage = currentUserMessageRef.current;
      setSession((prev) => {
        if (!prev) return null;
        const hasUserMessage = prev.messages.some(m => m.id === savedUserMessage?.id);
        const baseMessages = hasUserMessage ? prev.messages : [...prev.messages, savedUserMessage!];
        return { ...prev, messages: [...baseMessages, assistantMessage] };
      });
      setStreamingContent("");
      currentUserMessageRef.current = null;

      clearInterval(titleRefreshInterval);
      window.dispatchEvent(new Event("explorer-sessions-update"));
      setTimeout(() => {
        window.dispatchEvent(new Event("explorer-sessions-update"));
      }, TITLE_REFRESH_DELAYED_MS);
    } catch (error) {
      clearInterval(titleRefreshInterval);
      if ((error as Error).name === "AbortError") {
        if (fullContent) {
          let savedContent = fullContent;
          if (savedContent.includes("<think>") && !savedContent.includes("</think>")) {
            savedContent = savedContent + "</think>";
          }
          const assistantMessage: ExplorerMessage = {
            id: `assistant-${Date.now()}`,
            session_id: sessionId,
            role: "assistant",
            content: savedContent,
            created_at: new Date().toISOString(),
          };
          const savedUserMessage = currentUserMessageRef.current;
          setSession((prev) => {
            if (!prev) return null;
            const hasUserMessage = prev.messages.some(m => m.id === savedUserMessage?.id);
            const baseMessages = hasUserMessage ? prev.messages : [...prev.messages, savedUserMessage!];
            return { ...prev, messages: [...baseMessages, assistantMessage] };
          });
        }
        setStreamingContent("");
        currentUserMessageRef.current = null;
      } else {
        console.error("Failed to send message:", error);
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  }, [sessionId, isStreaming]);


  useEffect(() => {
    if (!session || pendingMessageProcessedRef.current || !pendingUserMessage) return;

    const pendingKey = `explorer-pending-${sessionId}`;
    sessionStorage.removeItem(pendingKey);
    pendingMessageProcessedRef.current = true;
    sendMessageContent(pendingUserMessage);
  }, [session, sessionId, pendingUserMessage, sendMessageContent]);


  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container || !isUserNearBottomRef.current) return;
    container.scrollTop = container.scrollHeight;
  }, [session?.messages, streamingContent]);


  const handleMessagesScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const { scrollTop, scrollHeight, clientHeight } = container;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    const nearBottom = distanceFromBottom < 100;
    isUserNearBottomRef.current = nearBottom;
    setShowScrollToBottom(!nearBottom);
  }, []);


  const adjustTextareaHeight = useCallback(() => {
    const textarea = inputRef.current;
    if (!textarea) return;

    textarea.style.height = "auto";
    const lineHeight = 24;
    const maxLines = 8;
    const maxHeight = lineHeight * maxLines;
    const newHeight = Math.min(textarea.scrollHeight, maxHeight);
    textarea.style.height = `${newHeight}px`;
  }, []);


  useEffect(() => {
    adjustTextareaHeight();
  }, [input, adjustTextareaHeight]);


  const uploadFile = async (file: File): Promise<Attachment | null> => {
    const formData = new FormData();
    formData.append("file", file);

    try {
      const token = localStorage.getItem("magnus_token");
      const response = await fetch(`${API_BASE}/api/explorer/sessions/${sessionId}/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`Upload failed: ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      console.error("File upload error:", error);
      return null;
    }
  };


  const handlePaste = async (e: React.ClipboardEvent) => {
    const items = e.clipboardData.items;
    const files: File[] = [];

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }

    if (files.length === 0) return;

    e.preventDefault();
    setIsUploading(true);

    for (const file of files) {
      const attachment = await uploadFile(file);
      if (attachment) {
        setAttachments((prev) => [...prev, attachment]);
      }
    }

    setIsUploading(false);
  };


  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    setIsUploading(true);
    for (let i = 0; i < files.length; i++) {
      const attachment = await uploadFile(files[i]);
      if (attachment) {
        setAttachments((prev) => [...prev, attachment]);
      }
    }
    setIsUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };


  const scrollToBottom = () => {
    const container = scrollContainerRef.current;
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }
  };


  const removeAttachment = (index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  };


  const sendMessage = async (editIndex?: number) => {
    if (!sessionId) return;

    const textContent = editIndex !== undefined ? editingMessageContent.trim() : input.trim();
    if (!textContent && attachments.length === 0) return;
    if (isStreaming) return;

    const imageParts: string[] = [];
    const docParts: string[] = [];

    for (const att of attachments) {
      if (att.type === "image" && att.path) {
        imageParts.push(`[图片: ${att.filename}](file://${att.path})`);
      } else if (att.type === "text" && att.content) {
        docParts.push(`---\n📄 ${att.filename}\n---\n${att.content}`);
      }
    }

    let messageContent = "";
    if (imageParts.length > 0) {
      messageContent += imageParts.join("\n\n");
    }
    if (textContent) {
      messageContent += (messageContent ? "\n\n" : "") + textContent;
    }
    if (docParts.length > 0) {
      messageContent += (messageContent ? "\n\n" : "") + docParts.join("\n\n");
    }

    if (editIndex !== undefined) {
      setSession((prev) => {
        if (!prev) return null;
        const newMessages = prev.messages.slice(0, editIndex);
        return { ...prev, messages: newMessages };
      });
      setEditingMessageIndex(null);
      setEditingMessageContent("");
    }

    const userMessage: ExplorerMessage = {
      id: `temp-${Date.now()}`,
      session_id: sessionId,
      role: "user",
      content: messageContent,
      created_at: new Date().toISOString(),
    };

    setSession((prev) =>
      prev ? { ...prev, messages: [...prev.messages, userMessage] } : null
    );

    setInput("");
    setAttachments([]);
    setIsStreaming(true);
    setStreamingContent("");
    isUserNearBottomRef.current = true;

    abortControllerRef.current = new AbortController();
    let fullContent = "";

    const titleRefreshInterval = setInterval(() => {
      window.dispatchEvent(new Event("explorer-sessions-update"));
    }, TITLE_REFRESH_INTERVAL_MS);

    setTimeout(() => {
      window.dispatchEvent(new Event("explorer-sessions-update"));
    }, TITLE_REFRESH_INITIAL_MS);

    try {
      const token = localStorage.getItem("magnus_token");
      const response = await fetch(
        `${API_BASE}/api/explorer/sessions/${sessionId}/chat`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            content: messageContent,
            truncate_before: editIndex,
          }),
          signal: abortControllerRef.current.signal,
        }
      );

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          fullContent += chunk;
          setStreamingContent(fullContent);
        }
      }

      const assistantMessage: ExplorerMessage = {
        id: `assistant-${Date.now()}`,
        session_id: sessionId,
        role: "assistant",
        content: fullContent,
        created_at: new Date().toISOString(),
      };

      setSession((prev) =>
        prev ? { ...prev, messages: [...prev.messages, assistantMessage] } : null
      );
      setStreamingContent("");

      clearInterval(titleRefreshInterval);
      window.dispatchEvent(new Event("explorer-sessions-update"));
      setTimeout(() => {
        window.dispatchEvent(new Event("explorer-sessions-update"));
      }, TITLE_REFRESH_DELAYED_MS);
    } catch (error) {
      clearInterval(titleRefreshInterval);
      if ((error as Error).name === "AbortError") {
        if (fullContent) {
          let savedContent = fullContent;
          // 流式响应中断时，补全未闭合的 thinking 标签
          if (savedContent.includes("<think>") && !savedContent.includes("</think>")) {
            savedContent = savedContent + "</think>";
          }
          const assistantMessage: ExplorerMessage = {
            id: `assistant-${Date.now()}`,
            session_id: sessionId,
            role: "assistant",
            content: savedContent,
            created_at: new Date().toISOString(),
          };
          setSession((prev) =>
            prev ? { ...prev, messages: [...prev.messages, assistantMessage] } : null
          );
        }
        setStreamingContent("");
      } else {
        console.error("Failed to send message:", error);
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  };


  const stopStreaming = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  };


  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Escape" && isStreaming) {
      e.preventDefault();
      stopStreaming();
    } else if (e.key === "Enter" && !e.shiftKey && !isStreaming) {
      // On touch devices, Enter = newline (no hardware keyboard typically)
      const isTouchDevice = "ontouchstart" in window || navigator.maxTouchPoints > 0;
      if (isTouchDevice) return;
      e.preventDefault();
      sendMessage();
    }
  };


  const startEditingMessage = (index: number, content: string) => {
    setEditingMessageIndex(index);
    setEditingMessageContent(content);
  };


  const cancelEditingMessage = () => {
    setEditingMessageIndex(null);
    setEditingMessageContent("");
  };

  if (notFound) {
    return (
      <NotFound
        title={t("explorer.notFound")}
        description={t("explorer.notFoundDesc")}
        buttonText={t("explorer.returnToExplorer")}
        onBack={() => router.push("/explorer")}
      />
    );
  }

  if (!session) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-zinc-500" />
      </div>
    );
  }

  const hasMessages = session.messages.length > 0 || isStreaming || pendingUserMessage;

  if (!hasMessages) {
    return (
      <>
        {previewImage && (
          <ImagePreviewModal
            src={previewImage.src}
            alt={previewImage.alt}
            onClose={() => setPreviewImage(null)}
          />
        )}

        <div className="flex-1 flex flex-col items-center justify-center px-4">
          <div className="w-full max-w-3xl">
            {/* Title */}
            <div className="text-center mb-8">
              <h1 className="text-3xl font-bold mb-2">
                <span className="text-zinc-100">{t("explorer.tagline1")}</span>
                <span className="text-blue-500">{t("explorer.tagline2")}</span>
              </h1>
              <p className="text-zinc-500">Magnus · {t("nav.explorer")}</p>
            </div>

            {/* Input */}
            <div className="mb-16">
              {attachments.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-2">
                  {attachments.map((att, idx) => (
                    <div
                      key={idx}
                      className="flex items-center gap-2 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm"
                    >
                      {att.type === "image" ? (
                        <ImageIcon className="w-4 h-4 text-zinc-400" />
                      ) : (
                        <FileText className="w-4 h-4 text-zinc-400" />
                      )}
                      <span className="text-zinc-300 max-w-32 truncate">{att.filename}</span>
                      <button
                        onClick={() => removeAttachment(idx)}
                        className="text-zinc-500 hover:text-zinc-300"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <div className="relative flex items-end bg-zinc-900 border border-zinc-700 rounded-xl focus-within:border-zinc-600 transition-colors">
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  placeholder={isUploading ? t("explorer.uploading") : t("explorer.inputPlaceholder")}
                  rows={1}
                  className="custom-scrollbar flex-1 bg-transparent text-base md:text-sm text-zinc-100 placeholder-zinc-500 px-4 py-3 resize-none focus:outline-none overflow-y-auto"
                  style={{ minHeight: "48px", maxHeight: "192px" }}
                  disabled={isUploading}
                />
                <VoiceInputButton
                  onTranscript={(text) => setInput((prev) => prev + text)}
                  context={input}
                  disabled={isUploading}
                />
                <button
                  onClick={() => sendMessage()}
                  disabled={(!input.trim() && attachments.length === 0) || isUploading}
                  className="m-2 ml-0 p-2 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors active:scale-95"
                >
                  {isUploading ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : (
                    <ArrowUp className="w-5 h-5" />
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      {/* Image Preview Modal */}
      {previewImage && (
        <ImagePreviewModal
          src={previewImage.src}
          alt={previewImage.alt}
          onClose={() => setPreviewImage(null)}
        />
      )}

      {/* Messages */}
      <div
        ref={scrollContainerRef}
        onScroll={handleMessagesScroll}
        className="flex-1 min-h-0 overflow-y-auto px-4 py-6 explorer-scroll"
      >
        <div className="max-w-3xl mx-auto space-y-6 pb-20 md:pb-32 min-w-0">
          {/* Show pending user message only if it's not yet in session.messages */}
          {pendingUserMessage && !session.messages.some(m => m.role === "user" && m.content === pendingUserMessage) && (
            <div className="flex justify-end">
              <div className="group flex items-start gap-2">
                <div className="bg-blue-600/20 border border-blue-500/30 text-zinc-100 px-4 py-3 rounded-2xl rounded-br-md">
                  <p className="text-sm whitespace-pre-wrap">{pendingUserMessage}</p>
                </div>
                {user?.avatar_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={user.avatar_url}
                    alt={user.name}
                    className="w-7 h-7 rounded-full border border-zinc-700 object-cover flex-shrink-0 mt-1"
                  />
                ) : (
                  <div className="w-7 h-7 rounded-full bg-blue-600/30 text-blue-400 flex items-center justify-center text-xs font-medium border border-blue-500/30 flex-shrink-0 mt-1">
                    {user?.name?.substring(0, 1) || "U"}
                  </div>
                )}
              </div>
            </div>
          )}

          {session.messages.map((message, index) => (
            <div
              key={message.id}
              className={`flex ${
                message.role === "user" ? "justify-end" : "justify-start"
              }`}
            >
              {message.role === "user" ? (
                editingMessageIndex === index ? (
                  <div className="max-w-[85%] w-full">
                    <textarea
                      value={editingMessageContent}
                      onChange={(e) => setEditingMessageContent(e.target.value)}
                      className="explorer-scroll w-full bg-zinc-800 text-zinc-100 text-sm px-4 py-3 rounded-2xl border border-zinc-600 focus:outline-none focus:border-zinc-500 resize-none"
                      rows={3}
                      autoFocus
                    />
                    <div className="flex justify-end gap-2 mt-2">
                      <button
                        onClick={cancelEditingMessage}
                        className="px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200 transition-colors"
                      >
                        {t("common.cancel")}
                      </button>
                      <button
                        onClick={() => sendMessage(index)}
                        className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
                      >
                        {t("explorer.send")}
                      </button>
                    </div>
                  </div>
                ) : (
                  <UserMessageWithActions
                    message={message}
                    index={index}
                    isLastUserMessage={index === session.messages.map(m => m.role).lastIndexOf("user")}
                    sessionId={sessionId}
                    user={session.user ?? user}
                    onEdit={() => startEditingMessage(index, message.content)}
                    onImageClick={(src, alt) => setPreviewImage({ src, alt })}
                  />
                )
              ) : (
                <div className="group flex flex-col min-w-0">
                  <div className="flex items-start gap-2 max-w-[85%]">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src="/api/logo"
                      alt="Magnus"
                      className="w-7 h-7 rounded-full border border-violet-500/30 flex-shrink-0 mt-1"
                    />
                    <div className="text-zinc-300 min-w-0 overflow-hidden">
                      <MessageContent content={message.content} />
                    </div>
                  </div>
                  {!isStreaming && (
                    <MessageActions
                      content={message.content}
                      alwaysShow={index === session.messages.length - 1}
                      onRegenerate={() => {
                        const lastUserIndex = session.messages.map(m => m.role).lastIndexOf("user");
                        if (lastUserIndex >= 0) {
                          startEditingMessage(lastUserIndex, session.messages[lastUserIndex].content);
                        }
                      }}
                    />
                  )}
                </div>
              )}
            </div>
          ))}

          {isStreaming && streamingContent && (
            <div className="flex justify-start">
              <div className="flex items-start gap-2 max-w-[85%]">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src="/api/logo"
                  alt="Magnus"
                  className="w-7 h-7 rounded-full border border-violet-500/30 flex-shrink-0 mt-1"
                />
                <div className="text-zinc-300 min-w-0 overflow-hidden">
                  <StreamingContent content={streamingContent} />
                </div>
              </div>
            </div>
          )}

          {isStreaming && !streamingContent && (
            <div className="flex justify-start">
              <div className="flex items-start gap-2">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src="/api/logo"
                  alt="Magnus"
                  className="w-7 h-7 rounded-full border border-violet-500/30 flex-shrink-0"
                />
                <div className="flex items-center gap-2 text-zinc-500 mt-1">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span className="text-sm">{t("explorer.thinkingWait")}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Input */}
      <div className="relative px-4 pb-4 pt-2">
        <div className="absolute inset-x-0 bottom-full h-12 md:h-24 bg-gradient-to-t from-zinc-950 to-transparent pointer-events-none" />

        {/* Scroll to bottom */}
        {showScrollToBottom && (
          <div className="absolute inset-x-0 bottom-full flex justify-center pb-2 pointer-events-none">
            <button
              onClick={scrollToBottom}
              className="pointer-events-auto p-2 bg-zinc-800 border border-zinc-700 rounded-full shadow-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700 transition-all active:scale-95"
            >
              <ArrowDown className="w-4 h-4" />
            </button>
          </div>
        )}

        <div className="max-w-3xl mx-auto">
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/*,.txt,.md,.py,.json,.csv,.yaml,.yml,.toml,.xml,.html,.css,.js,.ts,.tsx,.jsx"
            className="hidden"
            onChange={handleFileSelect}
          />

          {/* Attachments Preview */}
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-2">
              {attachments.map((att, index) => (
                <div
                  key={index}
                  className="flex items-center gap-2 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm"
                >
                  {att.type === "image" ? (
                    <ImageIcon className="w-4 h-4 text-zinc-400" />
                  ) : (
                    <FileText className="w-4 h-4 text-zinc-400" />
                  )}
                  <span className="text-zinc-300 max-w-32 truncate">{att.filename}</span>
                  <button
                    onClick={() => removeAttachment(index)}
                    className="text-zinc-500 hover:text-zinc-300 active:scale-95"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="relative flex items-end bg-zinc-900 border border-zinc-700 rounded-xl focus-within:border-zinc-600 transition-colors">
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
              className="m-2 mr-0 p-2 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded-lg transition-colors disabled:opacity-50 active:scale-95"
              title={t("explorer.inputPlaceholder")}
            >
              <Paperclip className="w-4 h-4" />
            </button>
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder={isUploading ? t("explorer.uploading") : t("explorer.inputPlaceholder")}
              rows={1}
              className="custom-scrollbar flex-1 bg-transparent text-base md:text-sm text-zinc-100 placeholder-zinc-500 px-3 py-3 resize-none focus:outline-none overflow-y-auto"
              style={{ minHeight: "48px", maxHeight: "192px" }}
              disabled={isUploading}
            />
            {isStreaming ? (
              <button
                onClick={stopStreaming}
                className="m-2 p-2 bg-red-900/30 hover:bg-red-800/40 text-red-400 rounded-lg transition-colors active:scale-95"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
              </button>
            ) : (
              <>
                <VoiceInputButton
                  onTranscript={(text) => setInput((prev) => prev + text)}
                  context={
                    (session?.messages.slice(-6).map((m) => m.content).join("\n") || "") +
                    (input ? "\n" + input : "")
                  }
                  disabled={isUploading}
                />
                <button
                  onClick={() => sendMessage()}
                  disabled={(!input.trim() && attachments.length === 0) || isUploading}
                  className="m-2 ml-0 p-2 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors active:scale-95"
                >
                  {isUploading ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : (
                    <ArrowUp className="w-5 h-5" />
                  )}
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

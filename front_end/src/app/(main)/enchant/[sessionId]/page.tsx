// front_end/src/app/(main)/enchant/[sessionId]/page.tsx
"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowUp, Loader2, Pencil, ChevronDown, ChevronRight, Square, X, FileText, Image as ImageIcon, ThumbsUp, ThumbsDown, RotateCcw, Copy, Check } from "lucide-react";
import { client } from "@/lib/api";
import RenderMarkdown from "@/components/ui/render-markdown";
import type { EnchantSessionWithMessages, EnchantMessage } from "@/types/enchant";
import { API_BASE } from "@/lib/config";
import { useAuth } from "@/context/auth-context";


function parseThinkingContent(content: string): { thinking: string | null; response: string } {
  const thinkMatch = content.match(/^<think>([\s\S]*?)<\/think>\s*([\s\S]*)$/);
  if (thinkMatch) {
    return { thinking: thinkMatch[1], response: thinkMatch[2] };
  }
  return { thinking: null, response: content };
}


function ThinkingBlock({ content, defaultExpanded = true }: { content: string; defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded);

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
        <span className="text-sm">Thinking</span>
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
    const baseUrl = `${API_BASE}/api/enchant/files/${sessionId}/${fileName}`;
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


function MessageActions({ content, onRegenerate }: { content: string; onRegenerate?: () => void }) {
  const [liked, setLiked] = useState<boolean | null>(null);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const { response } = parseThinkingContent(content);
    await navigator.clipboard.writeText(response);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex items-center gap-1 mt-2 ml-9">
      <button
        onClick={() => setLiked(liked === true ? null : true)}
        className={`p-2 rounded-md transition-colors ${
          liked === true
            ? "text-green-400 bg-green-400/10"
            : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800"
        }`}
        title="Good response"
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
        title="Bad response"
      >
        <ThumbsDown className="w-4 h-4" />
      </button>
      <button
        onClick={onRegenerate}
        className="p-2 rounded-md text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
        title="Regenerate"
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
        title={copied ? "Copied!" : "Copy"}
      >
        {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
      </button>
    </div>
  );
}


interface Attachment {
  type: "image" | "text";
  filename: string;
  file_id?: string;
  path?: string;
  content?: string;
}


export default function SessionPage() {
  const params = useParams();
  const router = useRouter();
  const { user } = useAuth();
  const sessionId = params.sessionId as string;

  const [session, setSession] = useState<EnchantSessionWithMessages | null>(null);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState("");
  const [editingMessageIndex, setEditingMessageIndex] = useState<number | null>(null);
  const [editingMessageContent, setEditingMessageContent] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [previewImage, setPreviewImage] = useState<{ src: string; alt: string } | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);


  const fetchSession = useCallback(async () => {
    try {
      const data: EnchantSessionWithMessages = await client(`/api/enchant/sessions/${sessionId}`);
      setSession(data);
    } catch (error) {
      console.error("Failed to fetch session:", error);
      router.push("/enchant");
    }
  }, [sessionId, router]);


  useEffect(() => {
    fetchSession();
  }, [fetchSession]);


  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [session?.messages, streamingContent]);


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
      const response = await fetch(`${API_BASE}/api/enchant/sessions/${sessionId}/upload`, {
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


  const removeAttachment = (index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  };


  const sendMessage = async (editIndex?: number) => {
    if (!sessionId) return;

    const textContent = editIndex !== undefined ? editingMessageContent.trim() : input.trim();
    if (!textContent && attachments.length === 0) return;

    if (isStreaming) return;

    // Build message with images first, then text, then documents
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

    const userMessage: EnchantMessage = {
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

    abortControllerRef.current = new AbortController();
    let fullContent = "";

    try {
      const token = localStorage.getItem("magnus_token");
      const response = await fetch(
        `${API_BASE}/api/enchant/sessions/${sessionId}/chat`,
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

      const assistantMessage: EnchantMessage = {
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

      window.dispatchEvent(new Event("enchant-sessions-update"));

      // Refresh again after delay for async title generation
      setTimeout(() => {
        window.dispatchEvent(new Event("enchant-sessions-update"));
      }, 3000);
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        // Save partial content if any was received
        if (fullContent) {
          let savedContent = fullContent;
          // If thinking tag was opened but not closed, close it
          if (savedContent.includes("<think>") && !savedContent.includes("</think>")) {
            savedContent = savedContent + "</think>";
          }
          const assistantMessage: EnchantMessage = {
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

  if (!session) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-zinc-500" />
      </div>
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
      <div className="flex-1 overflow-y-auto px-4 py-6 enchant-scroll">
        <div className="max-w-3xl mx-auto space-y-6 pb-32">
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
                      className="enchant-scroll w-full bg-zinc-800 text-zinc-100 text-sm px-4 py-3 rounded-2xl border border-zinc-600 focus:outline-none focus:border-zinc-500 resize-none"
                      rows={3}
                      autoFocus
                    />
                    <div className="flex justify-end gap-2 mt-2">
                      <button
                        onClick={cancelEditingMessage}
                        className="px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200 transition-colors"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => sendMessage(index)}
                        className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
                      >
                        Send
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="group flex items-start gap-2">
                    <button
                      onClick={() => startEditingMessage(index, message.content)}
                      className="opacity-0 group-hover:opacity-100 p-1.5 hover:bg-zinc-800 rounded transition-all mt-2"
                    >
                      <Pencil className="w-3.5 h-3.5 text-zinc-500 hover:text-zinc-300" />
                    </button>
                    <div className="bg-blue-600/20 border border-blue-500/30 text-zinc-100 px-4 py-3 rounded-2xl rounded-br-md">
                      <UserMessageContent
                        content={message.content}
                        sessionId={sessionId}
                        onImageClick={(src, alt) => setPreviewImage({ src, alt })}
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
                )
              ) : (
                <div className="flex flex-col">
                  <div className="flex items-start gap-2 max-w-[85%]">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src="/api/logo"
                      alt="Magnus"
                      className="w-7 h-7 rounded-full border border-violet-500/30 flex-shrink-0 mt-1"
                    />
                    <div className="text-zinc-300">
                      <MessageContent content={message.content} />
                    </div>
                  </div>
                  {!isStreaming && index === session.messages.length - 1 && (
                    <MessageActions
                      content={message.content}
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
                <div className="text-zinc-300">
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
                <span className="text-sm">Thinking...</span>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className="relative px-4 pb-4 pt-2">
        <div className="absolute inset-x-0 bottom-full h-24 bg-gradient-to-t from-zinc-950 to-transparent pointer-events-none" />
        <div className="max-w-3xl mx-auto">
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
              placeholder={isUploading ? "上传中..." : "输入消息，可上传图片和文件"}
              rows={1}
              className="enchant-scroll flex-1 bg-transparent text-sm text-zinc-100 placeholder-zinc-500 px-4 py-3 resize-none focus:outline-none overflow-y-auto"
              style={{ minHeight: "48px", maxHeight: "192px" }}
              disabled={isUploading}
            />
            {isStreaming ? (
              <button
                onClick={stopStreaming}
                className="m-2 p-2 bg-red-900/30 hover:bg-red-800/40 text-red-400 rounded-lg transition-colors"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
              </button>
            ) : (
              <button
                onClick={() => sendMessage()}
                disabled={(!input.trim() && attachments.length === 0) || isUploading}
                className="m-2 p-2 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
              >
                {isUploading ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <ArrowUp className="w-5 h-5" />
                )}
              </button>
            )}
          </div>
          <p className="text-xs text-zinc-600 mt-2 text-center">
            您在 Magnus 平台上的活动记录会被收集并整理为科学语料，请注意隐私保护
          </p>
        </div>
      </div>
    </>
  );
}

"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { ArrowUp, Loader2, X, FileText, Image as ImageIcon } from "lucide-react";
import { client } from "@/lib/api";
import { API_BASE } from "@/lib/config";
import { useLanguage } from "@/context/language-context";
import type { ExplorerSession, Attachment } from "@/types/explore";


export default function ExplorePage() {
  const router = useRouter();
  const { t } = useLanguage();
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);


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


  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !isSending) {
      e.preventDefault();
      sendMessage();
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

    const tempSession: ExplorerSession = await client("/api/explore/sessions", {
      json: { title: "New Session" },
    });

    for (const file of files) {
      const attachment = await uploadFile(tempSession.id, file);
      if (attachment) {
        setAttachments((prev) => [...prev, attachment]);
      }
    }

    setIsUploading(false);
  };


  const uploadFile = async (sessionId: string, file: File): Promise<Attachment | null> => {
    const formData = new FormData();
    formData.append("file", file);

    try {
      const token = localStorage.getItem("magnus_token");
      const response = await fetch(`${API_BASE}/api/explore/sessions/${sessionId}/upload`, {
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


  const removeAttachment = (index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  };


  const sendMessage = async () => {
    const textContent = input.trim();
    if (!textContent && attachments.length === 0) return;
    if (isSending) return;

    setIsSending(true);

    try {
      const newSession: ExplorerSession = await client("/api/explore/sessions", {
        json: { title: "New Session" },
      });

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

      sessionStorage.setItem(`explore-pending-${newSession.id}`, messageContent);

      window.dispatchEvent(new Event("explorer-sessions-update"));
      router.push(`/explore/${newSession.id}`);
    } catch (error) {
      console.error("Failed to send message:", error);
      setIsSending(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-3xl">
        {/* Title */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold mb-2">
            <span className="text-zinc-100">{t("explorer.tagline1")}</span>
            <span className="text-blue-500">{t("explorer.tagline2")}</span>
          </h1>
          <p className="text-zinc-500">Magnus · Explorer</p>
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
              className="custom-scrollbar flex-1 bg-transparent text-sm text-zinc-100 placeholder-zinc-500 px-4 py-3 resize-none focus:outline-none overflow-y-auto"
              style={{ minHeight: "48px", maxHeight: "192px" }}
              disabled={isUploading || isSending}
            />
            <button
              onClick={sendMessage}
              disabled={(!input.trim() && attachments.length === 0) || isUploading || isSending}
              className="m-2 p-2 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
            >
              {isUploading || isSending ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                <ArrowUp className="w-5 h-5" />
              )}
            </button>
          </div>
          <p className="text-xs text-zinc-600 mt-2 text-center">
            {t("explorer.privacyNotice")}
          </p>
        </div>
      </div>
    </div>
  );
}

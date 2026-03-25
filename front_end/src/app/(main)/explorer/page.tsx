"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { client } from "@/lib/api";
import { API_BASE } from "@/lib/config";
import { useLanguage } from "@/context/language-context";
import { MessageInput } from "@/components/ui/message-input";
import type { ExplorerSession, Attachment } from "@/types/explore";


export default function ExplorePage() {
  const router = useRouter();
  const { t } = useLanguage();
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isSending, setIsSending] = useState(false);


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

    const tempSession: ExplorerSession = await client("/api/explorer/sessions", {
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


  const removeAttachment = (index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  };


  const sendMessage = async () => {
    const textContent = input.trim();
    if (!textContent && attachments.length === 0) return;
    if (isSending) return;

    setIsSending(true);

    try {
      const newSession: ExplorerSession = await client("/api/explorer/sessions", {
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

      sessionStorage.setItem(`explorer-pending-${newSession.id}`, messageContent);

      window.dispatchEvent(new Event("explorer-sessions-update"));
      router.push(`/explorer/${newSession.id}`);
    } catch (error) {
      console.error("Failed to send message:", error);
      setIsSending(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4 py-8">
      <div className="w-full max-w-3xl">
        {/* Title */}
        <div className="text-center mb-12">
          <h1 className="text-3xl md:text-4xl font-bold mb-2">
            <span className="text-zinc-100">{t("explorer.tagline1")}</span>
            <span className="text-blue-500">{t("explorer.tagline2")}</span>
          </h1>
          <p className="text-zinc-500">Magnus · {t("nav.explorer")}</p>
        </div>

        {/* Input */}
        <div>
          <MessageInput
            value={input}
            onChange={setInput}
            onSend={sendMessage}
            attachments={attachments}
            onRemoveAttachment={removeAttachment}
            onPaste={handlePaste}
            disabled={isUploading || isSending}
            placeholder={isUploading ? t("explorer.uploading") : t("explorer.inputPlaceholder")}
          />
        </div>
      </div>
    </div>
  );
}

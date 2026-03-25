// front_end/src/components/ui/message-input.tsx
"use client";

import { useRef, useEffect } from "react";
import { ArrowUp, Loader2, Square, X, FileText, Image as ImageIcon, Paperclip } from "lucide-react";
import { VoiceInputButton } from "@/components/ui/voice-input-button";


export interface MessageInputAttachment {
  type: string;
  filename: string;
}

interface MessageInputProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;

  // 附件预览（chips）
  attachments?: MessageInputAttachment[];
  onRemoveAttachment?: (index: number) => void;
  onPaste?: (e: React.ClipboardEvent) => void;

  // 提供 onFileSelect 则显示 📎 按钮
  onFileSelect?: (e: React.ChangeEvent<HTMLInputElement>) => void;
  fileAccept?: string;

  // 提供 onStopStreaming 且 isStreaming=true 则显示 ■ 停止按钮
  isStreaming?: boolean;
  onStopStreaming?: () => void;

  // 语音输入（始终显示）
  voiceContext?: string;

  disabled?: boolean;
  placeholder?: string;
}


const DEFAULT_FILE_ACCEPT = "image/*,.txt,.md,.py,.json,.csv,.yaml,.yml,.toml,.xml,.html,.css,.js,.ts,.tsx,.jsx";

export function MessageInput({
  value,
  onChange,
  onSend,
  attachments,
  onRemoveAttachment,
  onPaste,
  onFileSelect,
  fileAccept,
  isStreaming,
  onStopStreaming,
  voiceContext,
  disabled,
  placeholder,
}: MessageInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const valueRef = useRef(value);
  valueRef.current = value;

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 192)}px`;
  }, [value]);

  const canSend = !!(value.trim() || (attachments && attachments.length > 0)) && !disabled;

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Escape" && isStreaming && onStopStreaming) {
      e.preventDefault();
      onStopStreaming();
    } else if (e.key === "Enter" && !e.shiftKey && !isStreaming) {
      const isTouchDevice = "ontouchstart" in window || navigator.maxTouchPoints > 0;
      if (isTouchDevice) return;
      e.preventDefault();
      if (canSend) onSend();
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (onFileSelect) onFileSelect(e);
    e.target.value = "";
  };

  const resolvedVoiceContext = voiceContext !== undefined ? voiceContext : value;

  return (
    <>
      {/* Hidden file input */}
      {onFileSelect && (
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={fileAccept || DEFAULT_FILE_ACCEPT}
          className="hidden"
          onChange={handleFileChange}
        />
      )}

      {/* Attachment chips */}
      {attachments && attachments.length > 0 && (
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
              {onRemoveAttachment && (
                <button
                  onClick={() => onRemoveAttachment(idx)}
                  className="text-zinc-500 hover:text-zinc-300 active:scale-95"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Input bar */}
      <div className="relative flex items-end bg-zinc-900 border border-zinc-700 rounded-xl focus-within:border-zinc-600 transition-colors">
        {/* 📎 button */}
        {onFileSelect && (
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled}
            className="m-2 mr-0 p-2 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded-lg transition-colors disabled:opacity-50 active:scale-95"
            title={placeholder}
          >
            <Paperclip className="w-4 h-4" />
          </button>
        )}

        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={onPaste}
          placeholder={placeholder}
          rows={1}
          className={`custom-scrollbar flex-1 bg-transparent text-base md:text-sm text-zinc-100 placeholder-zinc-500 ${
            onFileSelect ? "px-3" : "px-4"
          } py-3 resize-none focus:outline-none overflow-y-auto`}
          style={{ minHeight: "48px", maxHeight: "192px" }}
          disabled={disabled}
        />

        {/* Right buttons */}
        {isStreaming && onStopStreaming ? (
          <button
            onClick={onStopStreaming}
            className="m-2 p-2 bg-red-900/30 hover:bg-red-800/40 text-red-400 rounded-lg transition-colors active:scale-95"
          >
            <Square className="w-3.5 h-3.5 fill-current" />
          </button>
        ) : (
          <>
            <VoiceInputButton
              onTranscript={(text) => onChange(valueRef.current + text)}
              context={resolvedVoiceContext}
              disabled={disabled}
            />
            <button
              onClick={onSend}
              disabled={!canSend}
              className="m-2 ml-0 p-2 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors active:scale-95"
            >
              {disabled ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                <ArrowUp className="w-5 h-5" />
              )}
            </button>
          </>
        )}
      </div>
    </>
  );
}

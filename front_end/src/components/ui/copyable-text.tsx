// front_end/src/components/ui/copyable-text.tsx
"use client";

import { useState } from "react";
import { Copy, Check, X } from "lucide-react";

interface CopyableTextProps {
  text: string;
  copyValue?: string;
  label?: string;
  className?: string;
  variant?: "id" | "text"; 
}

export function CopyableText({
  text,
  copyValue,
  label,
  className = "",
  variant = "id"
}: CopyableTextProps) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const valueToCopy = copyValue || text;

  // 1. 传统兼容方案 (Fallback): 用于 HTTP 环境
  const copyLegacy = (content: string) => {
    const textArea = document.createElement("textarea");
    textArea.value = content;
    
    // 移出可视区域但保持在 DOM 中
    textArea.style.position = "fixed"; 
    textArea.style.left = "-9999px";
    textArea.style.top = "0";
    
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    
    try {
      document.execCommand('copy');
      return true;
    } catch (err) {
      console.error("Legacy copy failed", err);
      return false;
    } finally {
      document.body.removeChild(textArea);
    }
  };

  // 2. 主处理逻辑: 优先现代 API -> 失败则降级
  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    
    let success = false;

    // 检测是否支持现代 API (HTTPS/Localhost)
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(valueToCopy);
        success = true;
      } catch (err) {
        // 权限拒绝等情况，尝试降级
        success = copyLegacy(valueToCopy);
      }
    } else {
      // HTTP 环境，直接使用降级方案
      success = copyLegacy(valueToCopy);
    }

    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } else {
      setCopyFailed(true);
      setTimeout(() => setCopyFailed(false), 2000);
    }
  };

  const baseStyles = variant === "id" 
    ? "text-xs text-zinc-500 font-mono" 
    : "w-full";
    
  return (
    <button 
      onClick={handleCopy}
      className={`flex items-start text-left gap-1.5 transition-colors group/copy ${baseStyles} hover:text-blue-400 ${className}`}
      title="Click to copy"
    >
      {label && <span className="text-zinc-600 flex-shrink-0">{label}</span>}
      <span className={`min-w-0 ${variant === "text" ? "whitespace-normal break-all" : "truncate"}`}>
        {text}
      </span>
      <div className="flex-shrink-0 mt-[0.15em]">
        {copied ? (
          <Check className="w-3.5 h-3.5 text-green-500" />
        ) : copyFailed ? (
          <X className="w-3.5 h-3.5 text-red-500" />
        ) : (
          <Copy className="w-3.5 h-3.5 opacity-0 group-hover/copy:opacity-100 transition-opacity" />
        )}
      </div>
    </button>
  );
}
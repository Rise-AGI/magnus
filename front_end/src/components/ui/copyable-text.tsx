// front_end/src/components/ui/copyable-text.tsx
"use client";

import { useState } from "react";
import { Copy, Check } from "lucide-react";

interface CopyableTextProps {
  text: string;           // 显示的文本
  copyValue?: string;     // 实际复制的值（如果不传，默认复制显示的文本）
  label?: string;         // 可选的前缀标签 (例如 "ID: ")
  className?: string;     // 允许外部微调样式
}

export function CopyableText({ text, copyValue, label, className = "" }: CopyableTextProps) {
  const [copied, setCopied] = useState(false);
  const valueToCopy = copyValue || text;

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(valueToCopy);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button 
      onClick={handleCopy} 
      className={`flex items-center gap-1.5 text-xs text-zinc-500 hover:text-blue-400 transition-colors group/copy ${className}`}
      title="Click to copy"
    >
      {label && <span className="text-zinc-600">{label}</span>}
      <span className="font-mono">{text}</span>
      {copied ? (
        <Check className="w-3 h-3 text-green-500" />
      ) : (
        <Copy className="w-3 h-3 opacity-0 group-hover/copy:opacity-100 transition-opacity" />
      )}
    </button>
  );
}
// front_end/src/components/ui/config-clipboard.tsx
"use client";

import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { Copy, ClipboardPaste, Check, XCircle, ShieldAlert, X } from "lucide-react";
import { CopyableText } from "./copyable-text";

interface ConfigClipboardProps {
  kind: "magnus/service" | "magnus/job" | "magnus/blueprint";
  onGetPayload: () => any;
  onApplyPayload: (payload: any) => void;
}

export function ConfigClipboard({ kind, onGetPayload, onApplyPayload }: ConfigClipboardProps) {
  const [status, setStatus] = useState<"idle" | "copied" | "pasted" | "error" | "config_needed">("idle");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const handleCopy = async () => {
    try {
      const payload = onGetPayload();
      const envelope = JSON.stringify({ 
        kind, 
        version: "1.0", 
        payload, 
        exported_at: new Date().toISOString() 
      }, null, 2);

      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(envelope);
      } else {
        // HTTP Context Fallback for Write
        const textArea = document.createElement("textarea");
        textArea.value = envelope;
        textArea.style.position = "fixed";
        textArea.style.left = "-9999px";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
      }
      setStatus("copied");
      setTimeout(() => setStatus("idle"), 2000);
    } catch (e) {
      console.error("Export failed", e);
      setStatus("error");
      setTimeout(() => setStatus("idle"), 2000);
    }
  };

  const handlePaste = async () => {
    // 1. Check API Availability
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      setStatus("config_needed");
      return;
    }

    try {
      // 2. Attempt Read (Will throw in insecure context)
      const text = await navigator.clipboard.readText();
      
      // 3. Data Validation
      if (!text || !text.trim()) {
        setStatus("error");
        setTimeout(() => setStatus("idle"), 2000);
        return; 
      }

      let data;
      try { 
        data = JSON.parse(text); 
      } catch { 
        // Invalid JSON -> Data Error, not Config Error
        console.error("Invalid JSON in clipboard");
        setStatus("error");
        setTimeout(() => setStatus("idle"), 2000);
        return; 
      }

      const payloadToApply = data.payload || data;
      onApplyPayload(payloadToApply);
      
      setStatus("pasted");
      setTimeout(() => setStatus("idle"), 2000);

    } catch (e) {
      // 4. Permission / Context Error
      console.warn("Clipboard read failed (likely secure context issue)", e);
      setStatus("config_needed");
    }
  };

  const btnClass = "p-2 rounded-md hover:bg-zinc-800 text-zinc-400 hover:text-white transition-all active:scale-95 flex-shrink-0 relative";

  return (
    <div className="relative flex items-center gap-1">
      <button onClick={handleCopy} className={btnClass} title="Copy Config">
        {status === "copied" ? <Check className="w-4 h-4 text-green-500" /> : <Copy className="w-4 h-4" />}
      </button>

      <button onClick={handlePaste} className={btnClass} title="Paste Config">
        {status === "pasted" ? (
          <Check className="w-4 h-4 text-blue-500" />
        ) : status === "error" ? (
          <XCircle className="w-4 h-4 text-red-500" />
        ) : status === "config_needed" ? (
          <ShieldAlert className="w-4 h-4 text-amber-500 animate-pulse" />
        ) : (
          <ClipboardPaste className="w-4 h-4" />
        )}
      </button>

      {/* Browser Config Guide Modal */}
      {status === "config_needed" && mounted && createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
          <div 
            className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200" 
            onClick={() => setStatus("idle")}
          />
          
          <div className="relative bg-zinc-900 border border-amber-500/30 shadow-2xl rounded-xl w-full max-w-md overflow-hidden animate-in zoom-in-95 duration-200">
            {/* Header */}
            <div className="px-5 py-4 border-b border-zinc-800/50 flex items-start justify-between bg-zinc-900/50">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-amber-500/10 rounded-lg">
                        <ShieldAlert className="w-6 h-6 text-amber-500" />
                    </div>
                    <div>
                        <h3 className="text-base font-bold text-zinc-100">需要开启浏览器权限</h3>
                        <p className="text-xs text-zinc-400 mt-0.5">HTTP 环境下的剪贴板安全限制</p>
                    </div>
                </div>
                <button onClick={() => setStatus("idle")} className="text-zinc-500 hover:text-zinc-300 transition-colors">
                  <X className="w-5 h-5" />
                </button>
            </div>

            {/* Body */}
            <div className="p-5 space-y-4">
               <p className="text-sm text-zinc-300 leading-relaxed">
                  为了在内网 HTTP 环境下实现 <span className="text-amber-400 font-medium">“一键粘贴”</span>，请按照以下步骤将 Magnus 添加到浏览器白名单：
               </p>

               <div className="bg-black/40 rounded-lg border border-zinc-800/50 p-4 space-y-5">
                  {/* Step 1 */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                        <div className="flex items-center gap-2">
                            <span className="flex items-center justify-center w-5 h-5 rounded-full bg-zinc-800 text-[10px] font-bold text-zinc-400">1</span>
                            <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Flag Address</span>
                        </div>
                        <span className="text-[10px] text-zinc-600">(浏览器禁止直接跳转)</span>
                    </div>
                    <CopyableText 
                        text="chrome://flags/#unsafely-treat-insecure-origin-as-secure" 
                        className="text-blue-400 text-xs font-mono bg-blue-500/10 px-2 py-2 rounded-md w-full break-all border border-blue-500/20" 
                        variant="text" 
                    />
                    <p className="text-[10px] text-zinc-500 mt-1.5 ml-7">
                        请复制上方地址，粘贴到浏览器地址栏并回车。
                    </p>
                  </div>
                  
                  {/* Step 2 */}
                  <div>
                    <div className="flex items-center gap-2 mb-1.5">
                        <span className="flex items-center justify-center w-5 h-5 rounded-full bg-zinc-800 text-[10px] font-bold text-zinc-400">2</span>
                        <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Origin URL</span>
                    </div>
                    <CopyableText 
                        text={typeof window !== 'undefined' ? window.location.origin : ""} 
                        className="text-green-400 text-xs font-mono bg-green-500/10 px-2 py-2 rounded-md w-full border border-green-500/20" 
                    />
                    <p className="text-[10px] text-zinc-500 mt-1.5 ml-7">
                        将此地址填入高亮的文本框中，选择 <span className="text-zinc-300 font-bold">Enabled</span> 并重启浏览器。
                    </p>
                  </div>
               </div>
            </div>

            {/* Footer */}
            <div className="px-5 py-4 bg-zinc-950/50 border-t border-zinc-800/50 flex justify-end">
              <button 
                onClick={() => setStatus("idle")}
                className="px-4 py-2 bg-zinc-100 hover:bg-white text-zinc-900 text-sm font-semibold rounded-lg transition-colors shadow-lg shadow-zinc-900/20 active:scale-[0.98]"
              >
                我知道了
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
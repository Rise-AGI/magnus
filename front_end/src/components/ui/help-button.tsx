// front_end/src/components/ui/help-button.tsx
"use client";

import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { HelpCircle, X } from "lucide-react";
import { useLanguage } from "@/context/language-context";

interface HelpButtonProps {
  title: string;
  children: React.ReactNode;
}

export function HelpButton({ title, children }: HelpButtonProps) {
  const { t } = useLanguage();
  const [isOpen, setIsOpen] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!isOpen) return;

    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };

    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [isOpen]);

  const btnClass = "p-2 rounded-md hover:bg-zinc-800 text-zinc-400 hover:text-white transition-all active:scale-95 flex-shrink-0";

  return (
    <>
      <button
        onClick={() => setIsOpen(true)}
        className={btnClass}
        title="Help"
      >
        <HelpCircle className="w-4 h-4" />
      </button>

      {isOpen && mounted && createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center p-6">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200"
            onClick={() => setIsOpen(false)}
          />

          <div className="relative bg-zinc-900 border border-zinc-700/50 shadow-2xl rounded-xl w-full max-w-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            {/* Header */}
            <div className="px-6 py-4 border-b border-zinc-800/50 flex items-center justify-between bg-zinc-900/50">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-blue-500/10 rounded-lg">
                  <HelpCircle className="w-5 h-5 text-blue-500" />
                </div>
                <h3 className="text-base font-bold text-zinc-100">{title}</h3>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Body */}
            <div className="p-6 max-h-[70vh] overflow-y-auto custom-scrollbar">
              <div className="text-sm text-zinc-300 leading-relaxed space-y-5">
                {children}
              </div>
            </div>

            {/* Footer */}
            <div className="px-6 py-4 bg-zinc-950/50 border-t border-zinc-800/50 flex justify-end">
              <button
                onClick={() => setIsOpen(false)}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold rounded-lg transition-colors shadow-lg shadow-blue-900/20 active:scale-[0.98]"
              >
                {t("common.gotIt")}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  );
}

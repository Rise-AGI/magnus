"use client";

import React, { useEffect } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  description?: string;
  children: React.ReactNode;
  width?: string;
  icon?: React.ReactNode;
  footer?: React.ReactNode;
  actions?: React.ReactNode;
}

export function Drawer({
  isOpen,
  onClose,
  title,
  description,
  children,
  width = "w-[600px]",
  icon,
  actions,
}: DrawerProps) {
  
  // Handle ESC close
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) onClose();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [isOpen, onClose]);

  return (
    <>
      {/* Backdrop */}
      <div 
        className={cn(
          "fixed inset-0 bg-black/60 backdrop-blur-sm z-[90] transition-opacity duration-300",
          isOpen ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        )}
        onClick={onClose}
      />

      {/* Drawer Panel */}
      <div
        className={cn(
          "fixed top-0 right-0 h-full max-w-[95vw] bg-[#09090b] border-l border-zinc-800 shadow-2xl z-[100] transform transition-transform duration-300 ease-in-out flex flex-col",
          width,
          isOpen ? "translate-x-0" : "translate-x-full"
        )}
      >
        {/* Header */}
        <div className="px-6 py-5 border-b border-zinc-800 flex items-center justify-between bg-zinc-900/50 backdrop-blur-sm flex-shrink-0">
          <div className="flex-1 min-w-0 mr-4">
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              {icon && <span className="flex-shrink-0">{icon}</span>}
              <span className="truncate">{title}</span>
            </h2>
            {description && (
              <p className="text-xs text-zinc-500 mt-1 truncate">{description}</p>
            )}
          </div>
          
          <div className="flex items-center gap-2">
            {/* Action Buttons Area */}
            {actions && (
              <div className="flex items-center gap-1 border-r border-zinc-800 pr-2 mr-1">
                {actions}
              </div>
            )}

            <button 
              onClick={onClose} 
              className="text-zinc-500 hover:text-white transition-colors bg-zinc-800/50 hover:bg-zinc-700 p-1.5 rounded-md"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 custom-scrollbar relative">
          {children}
        </div>
      </div>
    </>
  );
}
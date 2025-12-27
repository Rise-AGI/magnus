// front_end/src/components/layout/header.tsx
"use client";

import { useState, useRef, useEffect } from "react";
import { Eye, EyeOff, PenLine } from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { client } from "@/lib/api";
import { NotificationsPopover } from "./notifications-popover";
import { CopyableText } from "@/components/ui/copyable-text";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";

export function Header() {
  const { user, isLoading } = useAuth();
  
  const [isOpen, setIsOpen] = useState(false);
  const [showToken, setShowToken] = useState(false);
  
  const [showResetDialog, setShowResetDialog] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setShowToken(false); 
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  const handleRefreshToken = async () => {
    setIsRefreshing(true);
    try {
      const token = localStorage.getItem("magnus_token");
      if (!token) throw new Error("No login token found.");
      
      const updatedUser = await client("/api/auth/token/refresh", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${token}`,
          "Content-Type": "application/json"
        }
      });

      localStorage.setItem("magnus_user", JSON.stringify(updatedUser));
      window.dispatchEvent(new Event("magnus-auth-change"));

      setShowResetDialog(false);
      setShowToken(false);
    } catch (error: any) {
      console.error("Refresh failed:", error);
      alert(`Failed to refresh token: ${error.message || "Unknown error"}`);
    } finally {
      setIsRefreshing(false);
    }
  };

  const realToken = user?.token || "sk-not-generated";
  const maskedToken = "sk-" + "•".repeat(24);
  const displayToken = showToken ? realToken : maskedToken;

  return (
    <>
      <header className="h-16 border-b border-zinc-800 bg-zinc-950/50 backdrop-blur sticky top-0 z-40 flex items-center justify-end px-8 gap-4">
        <NotificationsPopover />

        {!isLoading && user && (
          <div className="relative" ref={popoverRef}>
            <button 
              onClick={() => setIsOpen(!isOpen)}
              className={`flex items-center gap-3 pl-4 border-l border-zinc-800 transition-colors group outline-none
                ${isOpen ? "opacity-100" : "opacity-90 hover:opacity-100"}`}
            >
              <div className="text-right hidden md:block">
                <div className="text-sm font-medium text-zinc-200 leading-none mb-1 group-hover:text-blue-400 transition-colors">
                  {user.name}
                </div>
                <div className="text-xs text-zinc-500 font-mono">
                  {user.email || "PKU-PHY"}
                </div>
              </div>
              
              <div className={`w-8 h-8 rounded-full bg-zinc-800 border flex items-center justify-center text-zinc-400 overflow-hidden shadow-sm transition-all
                ${isOpen ? "border-blue-500/50 ring-2 ring-blue-500/20" : "border-zinc-700/50 group-hover:border-zinc-600"}`}>
                {user.avatar_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={user.avatar_url} alt={user.name} className="w-full h-full object-cover" />
                ) : (
                  <span className="text-xs font-bold">{user.name.substring(0, 1).toUpperCase()}</span>
                )}
              </div>
            </button>

            {isOpen && (
              <div className="absolute top-full right-0 mt-3 w-72 bg-zinc-950 border border-zinc-800 rounded-xl shadow-2xl ring-1 ring-white/5 p-1.5 animate-in fade-in slide-in-from-top-2 duration-200">
                <div className="flex items-center gap-1 bg-zinc-900/50 rounded-lg border border-zinc-800/50 px-2 py-1.5">
                  <div className="flex-1 min-w-0">
                    <CopyableText 
                      text={displayToken} 
                      copyValue={realToken}
                      variant="id"
                      className="w-full !text-zinc-400 hover:!text-blue-400"
                    />
                  </div>
                  
                  <div className="w-px h-3.5 bg-zinc-800 mx-1"></div>

                  <button
                    onClick={(e) => { e.stopPropagation(); setShowToken(!showToken); }}
                    className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-all"
                    title={showToken ? "Hide Token" : "Show Token"}
                  >
                    {showToken ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                  </button>

                  <button
                    onClick={(e) => { e.stopPropagation(); setShowResetDialog(true); }}
                    className="p-1.5 text-zinc-500 hover:text-blue-400 hover:bg-blue-500/10 rounded-md transition-all"
                    title="Reset Token"
                  >
                    <PenLine className="w-3.5 h-3.5" />
                  </button>
                </div>
                
                <div className="absolute -top-1.5 right-3 w-3 h-3 bg-zinc-950 border-t border-l border-zinc-800 rotate-45"></div>
              </div>
            )}
          </div>
        )}
      </header>

      <ConfirmationDialog 
        isOpen={showResetDialog}
        onClose={() => setShowResetDialog(false)}
        onConfirm={handleRefreshToken}
        title="Reset Trust Token?"
        description={
          <span>
            Are you sure you want to reset your Trust Token? <br/><br/>
            <span className="text-red-400">The current token will become invalid immediately.</span> 
            {" "}You will need to update your trust settings on the cluster.
          </span>
        }
        confirmText="Reset Token"
        variant="danger"
        isLoading={isRefreshing}
      />
    </>
  );
}
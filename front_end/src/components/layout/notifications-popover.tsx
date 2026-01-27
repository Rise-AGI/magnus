// front_end/src/components/layout/notifications-popover.tsx
"use client";

import { useState, useEffect, useRef } from "react";
import { Bell, Check, Info, AlertCircle, X, CheckCheck } from "lucide-react";
import { useLanguage } from "@/context/language-context";

// --- Types ---
// ! PROTECTED: Schema aligned with potential backend response.
export interface Notification {
  id: string;
  type: "info" | "success" | "error" | "warning";
  titleKey?: string;
  messageKey?: string;
  title?: string;
  message?: string;
  created_at: string;
  read: boolean;
}

export function NotificationsPopover() {
  const { t } = useLanguage();
  const [isOpen, setIsOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const popoverRef = useRef<HTMLDivElement>(null);

  // --- Logic: Load Data ---
  useEffect(() => {
    // TODO: Replace with API call: client("/api/notifications")
    setNotifications([
      {
        id: "init-1",
        type: "info",
        titleKey: "notifications.welcome",
        messageKey: "notifications.systemInit",
        created_at: new Date().toISOString(),
        read: false,
      }
    ]);
  }, []);

  // --- Logic: Update Count ---
  useEffect(() => {
    setUnreadCount(notifications.filter(n => !n.read).length);
  }, [notifications]);

  // --- Logic: Click Outside to Close ---
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const markAllAsRead = () => {
    setNotifications(prev => prev.map(n => ({ ...n, read: true })));
  };

  const removeNotification = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setNotifications(prev => prev.filter(n => n.id !== id));
  };

  const getIcon = (type: string) => {
    switch (type) {
      case "success": return <Check className="w-4 h-4 text-green-400" />;
      case "error": return <AlertCircle className="w-4 h-4 text-red-400" />;
      case "warning": return <AlertCircle className="w-4 h-4 text-yellow-400" />;
      default: return <Info className="w-4 h-4 text-blue-400" />;
    }
  };

  const formatTime = (isoString: string) => {
    return new Date(isoString).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div className="relative" ref={popoverRef}>
      {/* Trigger Button */}
      <button 
        onClick={() => setIsOpen(!isOpen)}
        className={`p-2 rounded-lg transition-all relative
          ${isOpen ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:text-white hover:bg-zinc-800/50'}`}
      >
        <Bell className="w-4 h-4" />
        {unreadCount > 0 && (
          <span className="absolute top-2 right-2 w-2 h-2 bg-blue-500 rounded-full border-2 border-[#0A0A0C]"></span>
        )}
      </button>

      {/* Popover Panel */}
      {isOpen && (
        <div className="absolute right-0 mt-2 w-80 bg-[#0A0A0C] border border-zinc-800 rounded-xl shadow-2xl z-50 overflow-hidden ring-1 ring-white/5 animate-in fade-in zoom-in-95 duration-100">
          
          <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800 bg-zinc-900/50 backdrop-blur-sm">
            <h3 className="text-sm font-semibold text-zinc-200">{t("notifications.title")}</h3>
            {unreadCount > 0 && (
              <button
                onClick={markAllAsRead}
                className="text-[10px] flex items-center gap-1 text-zinc-500 hover:text-blue-400 transition-colors"
              >
                <CheckCheck className="w-3 h-3" /> {t("notifications.markRead")}
              </button>
            )}
          </div>

          <div className="max-h-[300px] overflow-y-auto custom-scrollbar">
            {notifications.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-zinc-500 gap-2">
                <Bell className="w-8 h-8 opacity-20" />
                <p className="text-xs">{t("notifications.empty")}</p>
              </div>
            ) : (
              <div className="divide-y divide-zinc-800/50">
                {notifications.map(n => (
                  <div 
                    key={n.id} 
                    className={`px-4 py-3 flex gap-3 group transition-colors relative
                      ${n.read ? 'bg-transparent' : 'bg-zinc-900/30 hover:bg-zinc-900/50'}`}
                  >
                    <div className={`mt-0.5 flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center border border-zinc-800 bg-zinc-900`}>
                      {getIcon(n.type)}
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex justify-between items-start mb-0.5">
                        <p className={`text-sm font-medium leading-none ${n.read ? 'text-zinc-400' : 'text-zinc-200'}`}>
                          {n.titleKey ? t(n.titleKey as any) : n.title}
                        </p>
                        <span className="text-[10px] text-zinc-600 font-mono flex-shrink-0 ml-2">
                          {formatTime(n.created_at)}
                        </span>
                      </div>
                      <p className="text-xs text-zinc-500 leading-relaxed line-clamp-2">
                        {n.messageKey ? t(n.messageKey as any) : n.message}
                      </p>
                    </div>

                    <button 
                      onClick={(e) => removeNotification(n.id, e)}
                      className="absolute right-2 top-2 p-1 rounded hover:bg-zinc-700 text-zinc-600 hover:text-zinc-300 opacity-0 group-hover:opacity-100 transition-all"
                    >
                      <X className="w-3 h-3" />
                    </button>
                    
                    {!n.read && (
                      <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-blue-500/50"></div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
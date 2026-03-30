"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useParams } from "next/navigation";
import { Send, Settings, MessageCircle, Loader2, ChevronUp, ImageIcon, ChevronDown, Wifi, WifiOff, X } from "lucide-react";
import { client } from "@/lib/api";
import { API_BASE } from "@/lib/config";
import { useLanguage } from "@/context/language-context";
import { useAuth } from "@/context/auth-context";
import { formatBeijingTime } from "@/lib/utils";
import { ConversationSettingsDrawer } from "@/components/chat/conversation-settings-drawer";
import { AvatarCircle } from "@/components/ui/user-avatar";
import type { ConversationDetail, PagedMessageResponse, OptimisticChatMessage } from "@/types/chat";

const MESSAGE_PAGE_SIZE = 50;
const TIME_GAP_MS = 5 * 60 * 1000;
const WS_RECONNECT_DELAY = 3000;


export default function ConversationPage() {
  const params = useParams();
  const conversationId = params.conversationId as string;
  const { t } = useLanguage();
  const { user: currentUser } = useAuth();

  const [conversation, setConversation] = useState<ConversationDetail | null>(null);
  const [messages, setMessages] = useState<OptimisticChatMessage[]>([]);
  const [inputText, setInputText] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);
  const [initialLoaded, setInitialLoaded] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [isUploadingImage, setIsUploadingImage] = useState(false);
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [newMessageWhileScrolled, setNewMessageWhileScrolled] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const hasMarkedReadRef = useRef(false);
  const lastSeenMessageTimeRef = useRef<string | null>(null);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    messagesEndRef.current?.scrollIntoView({ behavior });
    setShowScrollToBottom(false);
    setNewMessageWhileScrolled(false);
  }, []);

  // Fetch conversation detail
  useEffect(() => {
    client(`/api/conversations/${conversationId}`)
      .then(setConversation)
      .catch((e) => console.error("Failed to fetch conversation:", e));
  }, [conversationId]);

  // Backfill: fetch messages newer than 'since' and merge (no duplicates)
  const backfillMessages = useCallback(async (since: string) => {
    try {
      const data: PagedMessageResponse = await client(
        `/api/conversations/${conversationId}/messages/backfill?since=${encodeURIComponent(since)}`
      );
      if (data.items.length === 0) return;
      setMessages((prev) => {
        const existingIds = new Set(prev.map((m) => m.id));
        const newMsgs = data.items.filter((m) => !existingIds.has(m.id));
        if (newMsgs.length === 0) return prev;
        const merged = [...prev, ...newMsgs].sort(
          (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
        );
        if (isAtBottomRef.current) {
          setTimeout(() => scrollToBottom(), 50);
        }
        return merged;
      });
    } catch (e) {
      console.error("Failed to backfill messages:", e);
    }
  }, [conversationId, scrollToBottom]);

  // Fetch initial messages
  useEffect(() => {
    setMessages([]);
    setCurrentPage(1);
    setHasMore(true);
    setInitialLoaded(false);
    hasMarkedReadRef.current = false;
    lastSeenMessageTimeRef.current = null;
    setShowScrollToBottom(false);
    setNewMessageWhileScrolled(false);

    const thisConvId = conversationId;
    client(`/api/conversations/${conversationId}/messages?page=1&page_size=${MESSAGE_PAGE_SIZE}`)
      .then((data: PagedMessageResponse) => {
        if (!mountedRef.current || thisConvId !== conversationId) return;
        const msgs = data.items.slice().reverse();
        setMessages(msgs);
        setHasMore(data.items.length === MESSAGE_PAGE_SIZE && data.items.length < data.total);
        setInitialLoaded(true);
        if (msgs.length > 0) {
          lastSeenMessageTimeRef.current = msgs[msgs.length - 1].created_at;
        }
        setTimeout(() => scrollToBottom("auto"), 50);
        // REST mark_read on initial load（不依赖 WS 是否已连接）
        client(`/api/conversations/${conversationId}/read`, { method: "POST" }).catch(() => {});
      })
      .catch((e) => {
        console.error("Failed to fetch messages:", e);
        if (mountedRef.current) setInitialLoaded(true);
      });
  }, [conversationId, scrollToBottom]);

  // WebSocket connection
  const connectWs = useCallback(() => {
    if (!mountedRef.current) return;

    const token = localStorage.getItem("magnus_token");
    if (!token) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsHost = process.env.NODE_ENV === "development"
      ? `${window.location.hostname}:${process.env.NEXT_PUBLIC_BACK_END_PORT}`
      : window.location.host;
    const wsUrl = `${protocol}//${wsHost}/ws/chat?token=${encodeURIComponent(token)}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      const since = lastSeenMessageTimeRef.current;
      if (since) {
        backfillMessages(since);
      }
    };

    ws.onmessage = (event) => {
      let data: {
        type: string;
        conversation_id?: string;
        message?: OptimisticChatMessage;
        user_id?: string;
        user_name?: string;
      };
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      if (data.type === "new_message" && data.conversation_id === conversationId && data.message) {
        const newMsg = data.message;
        setMessages((prev) => {
          // Already present by real id (WS arrived twice or REST+WS)
          if (prev.some((m) => m.id === newMsg.id)) return prev;
          // Update backfill cursor
          if (new Date(newMsg.created_at).getTime() > new Date(lastSeenMessageTimeRef.current || 0).getTime()) {
            lastSeenMessageTimeRef.current = newMsg.created_at;
          }
          if (isAtBottomRef.current) {
            setTimeout(() => scrollToBottom(), 50);
            // 自动标记已读（用 REST 稳定性更好）
            client(`/api/conversations/${conversationId}/read`, { method: "POST" }).catch(() => {});
          } else {
            setShowScrollToBottom(true);
            if (newMsg.sender_id !== currentUser?.id) {
              setNewMessageWhileScrolled(true);
            }
          }
          return [...prev, newMsg];
        });
        window.dispatchEvent(new Event("chat-conversations-update"));
      }

      // 成员变动：重新拉取会话详情以保持成员列表最新
      if (
        (data.type === "member_added" || data.type === "member_removed") &&
        data.conversation_id === conversationId
      ) {
        client(`/api/conversations/${conversationId}`)
          .then(setConversation)
          .catch(() => {});
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      if (!mountedRef.current) return;
      reconnectTimerRef.current = setTimeout(connectWs, WS_RECONNECT_DELAY);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [conversationId, backfillMessages, scrollToBottom, currentUser?.id]);

  useEffect(() => {
    mountedRef.current = true;
    connectWs();
    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connectWs]);

  // Track scroll position
  const handleMessagesScroll = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    const atBottom = scrollHeight - scrollTop - clientHeight < 60;
    isAtBottomRef.current = atBottom;
    if (atBottom) {
      setShowScrollToBottom(false);
      setNewMessageWhileScrolled(false);
    }
  }, []);

  // Load older messages
  const loadMore = async () => {
    if (isLoadingMore || !hasMore) return;
    setIsLoadingMore(true);

    const thisConvId = conversationId; // 捕获当前会话 id，防止切换会话后污染新状态
    const container = messagesContainerRef.current;
    const prevScrollHeight = container?.scrollHeight || 0;
    const nextPage = currentPage + 1;

    try {
      const data: PagedMessageResponse = await client(
        `/api/conversations/${thisConvId}/messages?page=${nextPage}&page_size=${MESSAGE_PAGE_SIZE}`
      );
      if (!mountedRef.current || conversationId !== thisConvId) return;

      const olderMessages = data.items.slice().reverse();
      setMessages((prev) => {
        const existingIds = new Set(prev.map((m) => m.id));
        const unique = olderMessages.filter((m) => !existingIds.has(m.id));
        return [...unique, ...prev];
      });

      setCurrentPage(nextPage);
      setHasMore(data.items.length === MESSAGE_PAGE_SIZE);

      // 双 rAF 保证 DOM 渲染完成后再恢复滚动位置
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (container) container.scrollTop = container.scrollHeight - prevScrollHeight;
        });
      });
    } catch (e) {
      console.error("Failed to load more messages:", e);
    } finally {
      if (mountedRef.current) setIsLoadingMore(false);
    }
  };

  // Send message via REST (WS broadcast handles delivery to all devices/members)
  const handleSend = async () => {
    const text = inputText.trim();
    if (!text || isSending || !currentUser) return;

    setSendError(null);
    setIsSending(true);
    setInputText("");
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }

    // Optimistic update
    const tempId = `temp-${Date.now()}`;
    const optimisticMsg: OptimisticChatMessage = {
      id: tempId,
      conversation_id: conversationId,
      sender_id: currentUser.id,
      content: text,
      message_type: "text",
      created_at: new Date().toISOString(),
      sender: {
        id: currentUser.id,
        name: currentUser.name,
        avatar_url: currentUser.avatar_url,
        email: currentUser.email,
      },
      tempId,
      isOptimistic: true,
    };
    setMessages((prev) => [...prev, optimisticMsg]);
    setTimeout(() => scrollToBottom(), 50);

    try {
      const sent: OptimisticChatMessage = await client(`/api/conversations/${conversationId}/messages`, {
        json: { content: text, message_type: "text" },
      });
      setMessages((prev) => {
        // Remove optimistic; if WS already delivered the real message, avoid re-adding
        const withoutOptimistic = prev.filter((m) => m.id !== tempId);
        if (withoutOptimistic.some((m) => m.id === sent.id)) return withoutOptimistic;
        return [...withoutOptimistic, sent];
      });
      window.dispatchEvent(new Event("chat-conversations-update"));
    } catch (e) {
      console.error("Failed to send message:", e);
      setMessages((prev) => prev.filter((m) => m.id !== tempId));
      setInputText(text);
      setSendError(t("chat.sendFailed"));
      // 3 秒后自动消除错误提示
      setTimeout(() => setSendError(null), 3000);
    } finally {
      setIsSending(false);
      textareaRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value);
    const ta = e.target;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
  };

  const MAX_IMAGE_SIZE = 10 * 1024 * 1024; // 10 MB

  const handleImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (file.size > MAX_IMAGE_SIZE) {
      setSendError(t("chat.imageTooLarge"));
      setTimeout(() => setSendError(null), 3000);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    setIsUploadingImage(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const token = localStorage.getItem("magnus_token");
      const res = await fetch(`${API_BASE}/api/chat/media/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });
      if (!res.ok) throw new Error("Upload failed");
      const { url } = await res.json();
      const sent: OptimisticChatMessage = await client(
        `/api/conversations/${conversationId}/messages`,
        { json: { content: url, message_type: "image" } }
      );
      setMessages((prev) => {
        if (prev.some((m) => m.id === sent.id)) return prev;
        return [...prev, sent];
      });
      setTimeout(() => scrollToBottom(), 50);
      window.dispatchEvent(new Event("chat-conversations-update"));
    } catch (err) {
      console.error("Image upload failed:", err);
    } finally {
      setIsUploadingImage(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const shouldShowTimestamp = (msg: OptimisticChatMessage, prevMsg: OptimisticChatMessage | null): boolean => {
    if (!prevMsg) return true;
    return new Date(msg.created_at).getTime() - new Date(prevMsg.created_at).getTime() > TIME_GAP_MS;
  };

  const shouldShowAvatar = (msg: OptimisticChatMessage, prevMsg: OptimisticChatMessage | null): boolean => {
    if (!prevMsg) return true;
    return prevMsg.sender_id !== msg.sender_id || shouldShowTimestamp(msg, prevMsg);
  };

  const isGroup = conversation?.type === "group";

  const displayName = isGroup
    ? (conversation.name || t("chat.type.group"))
    : conversation?.members.find((m) => m.user_id !== currentUser?.id)?.user?.name || t("chat.type.p2p");

  const memberCount = conversation?.members.length || 0;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-5 py-3 border-b border-zinc-800/80 flex items-center justify-between bg-zinc-950/60 backdrop-blur-sm flex-shrink-0">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-zinc-200 truncate">{displayName}</h2>
            <div className="flex items-center gap-1.5 mt-0.5">
              <span className="text-[10px] text-zinc-600">
                {memberCount} {t("chat.members").toLowerCase()}
              </span>
              <span className="text-zinc-700">·</span>
              <div className="flex items-center gap-1">
                {isConnected ? (
                  <Wifi className="w-2.5 h-2.5 text-emerald-500" />
                ) : (
                  <WifiOff className="w-2.5 h-2.5 text-zinc-600" />
                )}
                <span className={`text-[10px] ${isConnected ? "text-emerald-600" : "text-zinc-600"}`}>
                  {isConnected ? t("chat.connected") : t("chat.reconnecting")}
                </span>
              </div>
            </div>
          </div>
        </div>
        {isGroup && (
          <button
            onClick={() => setShowSettings(true)}
            className="p-2 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded-lg transition-colors cursor-pointer flex-shrink-0"
          >
            <Settings className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Messages area */}
      <div className="flex-1 relative overflow-hidden">
        <div
          ref={messagesContainerRef}
          onScroll={handleMessagesScroll}
          className="absolute inset-0 overflow-y-auto px-4 py-4 custom-scrollbar"
        >
          {hasMore && initialLoaded && (
            <div className="flex justify-center mb-4">
              <button
                onClick={loadMore}
                disabled={isLoadingMore}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-zinc-500 hover:text-zinc-300 bg-zinc-900/70 hover:bg-zinc-800 border border-zinc-800 rounded-full transition-colors disabled:opacity-50 cursor-pointer"
              >
                {isLoadingMore ? <Loader2 className="w-3 h-3 animate-spin" /> : <ChevronUp className="w-3 h-3" />}
                {t("chat.loadMore")}
              </button>
            </div>
          )}

          {messages.length === 0 && initialLoaded && (
            <div className="flex flex-col items-center justify-center h-full gap-2">
              <div className="w-12 h-12 rounded-full bg-zinc-900 border border-zinc-800 flex items-center justify-center">
                <MessageCircle className="w-5 h-5 text-zinc-700" />
              </div>
              <p className="text-sm text-zinc-600">{t("chat.noMessages")}</p>
            </div>
          )}

          {messages.map((msg, idx) => {
            const prevMsg = idx > 0 ? messages[idx - 1] : null;
            const isMe = msg.sender_id === currentUser?.id;
            const showTime = shouldShowTimestamp(msg, prevMsg);
            const showAvatar = !isMe && shouldShowAvatar(msg, prevMsg);
            const showMyAvatar = isMe && shouldShowAvatar(msg, prevMsg);
            const showSenderName = !isMe && isGroup && (
              !prevMsg || prevMsg.sender_id !== msg.sender_id || shouldShowTimestamp(msg, prevMsg)
            );
            const isFirstInRun = !prevMsg || prevMsg.sender_id !== msg.sender_id || shouldShowTimestamp(msg, prevMsg);

            return (
              <div key={msg.id} className={msg.isOptimistic ? "opacity-70" : "animate-in fade-in duration-200"}>
                {showTime && (
                  <div className="flex items-center justify-center my-4 gap-3">
                    <div className="flex-1 h-px bg-zinc-800/60" />
                    <span className="text-[10px] text-zinc-600 px-1 flex-shrink-0 tabular-nums">
                      {formatBeijingTime(msg.created_at)}
                    </span>
                    <div className="flex-1 h-px bg-zinc-800/60" />
                  </div>
                )}

                <div className={`flex mb-1 ${isMe ? "justify-end" : "justify-start"} ${isFirstInRun ? "mt-3" : ""}`}>
                  {/* Left avatar (others) */}
                  {!isMe && (
                    <div className="w-7 flex-shrink-0 mr-2 self-start mt-0.5">
                      {showAvatar ? (
                        <AvatarCircle user={msg.sender} size="xs" />
                      ) : null}
                    </div>
                  )}

                  <div className={`max-w-[68%] ${isMe ? "items-end" : "items-start"} flex flex-col`}>
                    {showSenderName && (
                      <p className="text-[10px] text-zinc-500 mb-1 px-1">
                        {msg.sender?.name || "Unknown"}
                      </p>
                    )}
                    <div
                      className={`rounded-2xl text-sm leading-relaxed ${
                        msg.message_type === "image"
                          ? "overflow-hidden rounded-lg"
                          : `px-3.5 py-2 break-words whitespace-pre-wrap ${
                              isMe
                                ? "bg-blue-500/20 border border-blue-500/25 text-zinc-100 rounded-br-sm"
                                : "bg-zinc-800/70 border border-zinc-700/40 text-zinc-200 rounded-bl-sm"
                            }`
                      }`}
                    >
                      {msg.message_type === "image" ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={msg.content}
                          alt="image"
                          className="max-w-[260px] max-h-[260px] cursor-pointer object-cover hover:opacity-90 transition-opacity"
                          onClick={() => setPreviewImage(msg.content)}
                        />
                      ) : (
                        msg.content
                      )}
                    </div>
                  </div>

                  {/* Right avatar (my messages) */}
                  {isMe && (
                    <div className="w-7 flex-shrink-0 ml-2 self-start mt-0.5">
                      {showMyAvatar ? (
                        <AvatarCircle user={currentUser} size="xs" />
                      ) : null}
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          <div ref={messagesEndRef} />
        </div>

        {/* Scroll to bottom button */}
        {showScrollToBottom && (
          <button
            onClick={() => scrollToBottom()}
            className="absolute bottom-4 right-4 p-2 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded-full shadow-lg transition-all cursor-pointer"
          >
            <ChevronDown className="w-4 h-4 text-zinc-300" />
            {newMessageWhileScrolled && (
              <span className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-blue-500 rounded-full" />
            )}
          </button>
        )}
      </div>

      {/* Input area */}
      <div className="px-4 py-3 border-t border-zinc-800/80 bg-zinc-950/60 backdrop-blur-sm flex-shrink-0">
        {sendError && (
          <p className="text-xs text-red-400 mb-2 px-1 animate-in fade-in duration-200">{sendError}</p>
        )}
        <div className="flex items-end gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={handleImageUpload}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploadingImage}
            title={t("chat.uploadImage")}
            className="p-2 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors disabled:opacity-50 flex-shrink-0 cursor-pointer"
          >
            {isUploadingImage ? <Loader2 className="w-4 h-4 animate-spin" /> : <ImageIcon className="w-4 h-4" />}
          </button>
          <textarea
            ref={textareaRef}
            value={inputText}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            placeholder={t("chat.inputPlaceholder")}
            rows={1}
            className="flex-1 bg-zinc-900/80 border border-zinc-800 rounded-xl px-3.5 py-2.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-600 focus:ring-1 focus:ring-zinc-700/50 resize-none custom-scrollbar transition-colors"
            style={{ maxHeight: 120 }}
          />
          <button
            onClick={handleSend}
            disabled={!inputText.trim() || isSending}
            className="p-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0 cursor-pointer shadow-sm"
          >
            {isSending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Image preview overlay */}
      {previewImage && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center"
          onClick={() => setPreviewImage(null)}
        >
          <button
            className="absolute top-4 right-4 p-2 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-full transition-colors cursor-pointer"
            onClick={() => setPreviewImage(null)}
          >
            <X className="w-5 h-5" />
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={previewImage}
            alt="preview"
            className="max-w-[90vw] max-h-[90vh] rounded-lg object-contain"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}

      {conversation && (
        <ConversationSettingsDrawer
          isOpen={showSettings}
          onClose={() => setShowSettings(false)}
          conversation={conversation}
          onUpdate={(updated) => setConversation(updated)}
        />
      )}
    </div>
  );
}

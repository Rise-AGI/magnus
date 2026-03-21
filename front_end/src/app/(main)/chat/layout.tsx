"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import { Trash2, MessageCircle, Users, Loader2, Plus } from "lucide-react";
import { client } from "@/lib/api";
import { useLanguage } from "@/context/language-context";
import { useAuth } from "@/context/auth-context";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { formatRelativeTime } from "@/lib/utils";
import type { ConversationListItem, PagedConversationResponse } from "@/types/chat";

const PAGE_SIZE = 20;

const AVATAR_COLORS = [
  "bg-blue-500",
  "bg-violet-500",
  "bg-emerald-500",
  "bg-amber-500",
  "bg-rose-500",
  "bg-cyan-500",
  "bg-orange-500",
  "bg-teal-500",
];

function getAvatarColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) & 0xffffffff;
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

function ConvAvatar({ conv, currentUserId }: { conv: ConversationListItem; currentUserId?: string }) {
  if (conv.type === "group") {
    return (
      <div className="w-9 h-9 rounded-full bg-zinc-700 border border-zinc-600/50 flex items-center justify-center flex-shrink-0">
        <Users className="w-4 h-4 text-zinc-400" />
      </div>
    );
  }
  const other = conv.other_user;
  if (other?.avatar_url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={other.avatar_url}
        alt={other.name}
        className="w-9 h-9 rounded-full object-cover flex-shrink-0 border border-zinc-700/50"
      />
    );
  }
  const name = other?.name || "?";
  const colorClass = getAvatarColor(other?.id || conv.id);
  return (
    <div className={`w-9 h-9 rounded-full ${colorClass} flex items-center justify-center flex-shrink-0 text-white text-sm font-semibold`}>
      {name.charAt(0).toUpperCase()}
    </div>
  );
}


export default function ChatLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { t } = useLanguage();
  const { user: currentUser } = useAuth();
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [initialLoaded, setInitialLoaded] = useState(false);
  const [deletingConv, setDeletingConv] = useState<ConversationListItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const currentPage = useRef(1);

  const activeConversationId = pathname.startsWith("/chat/")
    ? pathname.split("/")[2]
    : null;

  const fetchConversations = useCallback(async (page: number = 1, append: boolean = false) => {
    try {
      const data: PagedConversationResponse = await client(
        `/api/conversations?page=${page}&page_size=${PAGE_SIZE}`
      );
      if (append) {
        setConversations((prev) => [...prev, ...data.items]);
      } else {
        setConversations(data.items);
      }
      setHasMore(data.items.length === PAGE_SIZE && (page - 1) * PAGE_SIZE + data.items.length < data.total);
      setInitialLoaded(true);
    } catch (error) {
      console.error("Failed to fetch conversations:", error);
      setInitialLoaded(true);
    }
  }, []);

  const loadMore = useCallback(async () => {
    if (isLoadingMore || !hasMore) return;
    setIsLoadingMore(true);
    currentPage.current += 1;
    await fetchConversations(currentPage.current, true);
    setIsLoadingMore(false);
  }, [isLoadingMore, hasMore, fetchConversations]);

  useEffect(() => {
    fetchConversations(1, false);
  }, [fetchConversations]);

  useEffect(() => {
    const handleUpdate = () => {
      currentPage.current = 1;
      setHasMore(true);
      fetchConversations(1, false);
    };
    window.addEventListener("chat-conversations-update", handleUpdate);
    return () => window.removeEventListener("chat-conversations-update", handleUpdate);
  }, [fetchConversations]);

  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container || isLoadingMore || !hasMore) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    if (scrollHeight - scrollTop - clientHeight < 100) {
      loadMore();
    }
  }, [isLoadingMore, hasMore, loadMore]);

  const deleteConversation = async (convId: string) => {
    try {
      await client(`/api/conversations/${convId}`, { method: "DELETE" });
      setConversations((prev) => prev.filter((c) => c.id !== convId));
      if (activeConversationId === convId) {
        router.push("/chat");
      }
    } catch (error) {
      console.error("Failed to delete conversation:", error);
    }
  };

  const getDisplayName = (conv: ConversationListItem): string => {
    if (conv.type === "group") return conv.name || t("chat.type.group");
    return conv.other_user?.name || conv.name || t("chat.type.p2p");
  };

  return (
    <div className="flex h-full w-full bg-zinc-950 overflow-hidden">
      {/* Conversation sidebar */}
      <div className="hidden md:flex w-60 flex-shrink-0 border-r border-zinc-800/80 flex-col">
        {/* Header */}
        <div className="px-4 py-3.5 flex items-center justify-between border-b border-zinc-800/50">
          <h3 className="text-sm font-semibold text-zinc-300 tracking-wide">{t("chat.conversations")}</h3>
          <button
            onClick={() => router.push("/chat")}
            title={t("chat.newConversation")}
            className="p-1.5 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 rounded-md transition-colors cursor-pointer"
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* List */}
        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto py-1.5 px-1.5 custom-scrollbar"
        >
          {conversations.map((conv) => {
            const isActive = activeConversationId === conv.id;
            const lastMsgText = conv.last_message
              ? conv.last_message.message_type === "image"
                ? t("chat.imageMessage")
                : conv.last_message.content
              : null;

            return (
              <div
                key={conv.id}
                className={`group flex items-center gap-2.5 px-2.5 py-2 rounded-lg cursor-pointer mb-0.5 transition-colors ${
                  isActive
                    ? "bg-zinc-800 text-zinc-100"
                    : "hover:bg-zinc-800/40 text-zinc-400"
                }`}
                onClick={() => router.push(`/chat/${conv.id}`)}
              >
                <ConvAvatar conv={conv} currentUserId={currentUser?.id} />

                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline justify-between gap-1">
                    <span className={`text-sm font-medium truncate ${isActive ? "text-zinc-100" : "text-zinc-300"}`}>
                      {getDisplayName(conv)}
                    </span>
                    <span className="text-[10px] text-zinc-600 flex-shrink-0 tabular-nums">
                      {formatRelativeTime(conv.last_message?.created_at || conv.updated_at)}
                    </span>
                  </div>
                  {lastMsgText && (
                    <p className="text-xs text-zinc-600 truncate mt-0.5 leading-snug">
                      {lastMsgText}
                    </p>
                  )}
                </div>

                {/* Delete (owner only) */}
                {conv.created_by === currentUser?.id && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setDeletingConv(conv);
                    }}
                    className="hidden group-hover:flex items-center justify-center p-1 hover:bg-zinc-700 rounded flex-shrink-0 cursor-pointer"
                  >
                    <Trash2 className="w-3.5 h-3.5 text-zinc-500 hover:text-red-400" />
                  </button>
                )}
              </div>
            );
          })}

          {conversations.length === 0 && initialLoaded && (
            <div className="flex flex-col items-center justify-center py-12 px-4">
              <MessageCircle className="w-8 h-8 text-zinc-700 mb-2" />
              <p className="text-xs text-zinc-600 text-center">{t("chat.noConversations")}</p>
            </div>
          )}

          {!initialLoaded && (
            <div className="flex justify-center py-8">
              <Loader2 className="w-4 h-4 animate-spin text-zinc-600" />
            </div>
          )}

          {isLoadingMore && (
            <div className="flex justify-center py-3">
              <Loader2 className="w-4 h-4 animate-spin text-zinc-600" />
            </div>
          )}
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-h-0 min-w-0">
        {children}
      </div>

      {/* Delete Confirm Dialog */}
      <ConfirmationDialog
        isOpen={!!deletingConv}
        onClose={() => setDeletingConv(null)}
        onConfirm={async () => {
          if (!deletingConv) return;
          setIsDeleting(true);
          await deleteConversation(deletingConv.id);
          setIsDeleting(false);
          setDeletingConv(null);
        }}
        title={t("chat.deleteConversation")}
        description={t("chat.deleteConfirm")}
        confirmText={t("common.delete")}
        isLoading={isDeleting}
        variant="danger"
      />
    </div>
  );
}

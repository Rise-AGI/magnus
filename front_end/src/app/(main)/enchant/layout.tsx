"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import { Trash2, MessageSquare, Pencil, Check, X, Loader2, Plus } from "lucide-react";
import { client } from "@/lib/api";
import type { EnchantSession, PagedEnchantSessionResponse } from "@/types/enchant";

const PAGE_SIZE = 20;


export default function EnchantLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [sessions, setSessions] = useState<EnchantSession[]>([]);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [initialLoaded, setInitialLoaded] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const activeSessionId = pathname.startsWith("/enchant/")
    ? pathname.split("/")[2]
    : null;


  const fetchSessions = useCallback(async (skip: number = 0, append: boolean = false) => {
    try {
      const data: PagedEnchantSessionResponse = await client(
        `/api/enchant/sessions?skip=${skip}&limit=${PAGE_SIZE}`
      );

      if (append) {
        setSessions((prev) => [...prev, ...data.items]);
      } else {
        setSessions(data.items);
      }

      setHasMore(data.items.length === PAGE_SIZE && skip + data.items.length < data.total);
      setInitialLoaded(true);
    } catch (error) {
      console.error("Failed to fetch sessions:", error);
    }
  }, []);


  const loadMore = useCallback(async () => {
    if (isLoadingMore || !hasMore) return;

    setIsLoadingMore(true);
    await fetchSessions(sessions.length, true);
    setIsLoadingMore(false);
  }, [isLoadingMore, hasMore, sessions.length, fetchSessions]);


  useEffect(() => {
    fetchSessions(0, false);
  }, [fetchSessions]);


  useEffect(() => {
    const handleSessionsUpdate = () => {
      setHasMore(true);
      fetchSessions(0, false);
    };
    window.addEventListener("enchant-sessions-update", handleSessionsUpdate);
    return () => window.removeEventListener("enchant-sessions-update", handleSessionsUpdate);
  }, [fetchSessions]);


  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container || isLoadingMore || !hasMore) return;

    const { scrollTop, scrollHeight, clientHeight } = container;
    if (scrollHeight - scrollTop - clientHeight < 100) {
      loadMore();
    }
  }, [isLoadingMore, hasMore, loadMore]);


  const deleteSession = async (sessionId: string) => {
    try {
      await client(`/api/enchant/sessions/${sessionId}`, { method: "DELETE" });
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (activeSessionId === sessionId) {
        router.push("/enchant");
      }
    } catch (error) {
      console.error("Failed to delete session:", error);
    }
  };


  const updateSessionTitle = async (sessionId: string, title: string) => {
    try {
      await client(`/api/enchant/sessions/${sessionId}`, {
        method: "PATCH",
        json: { title },
      });
      setSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? { ...s, title } : s))
      );
    } catch (error) {
      console.error("Failed to update session title:", error);
    }
  };


  const startEditing = (session: EnchantSession) => {
    setEditingSessionId(session.id);
    setEditingTitle(session.title);
  };


  const saveEditing = async () => {
    if (editingSessionId && editingTitle.trim()) {
      await updateSessionTitle(editingSessionId, editingTitle.trim());
    }
    setEditingSessionId(null);
  };


  const cancelEditing = () => {
    setEditingSessionId(null);
    setEditingTitle("");
  };

  return (
    <div className="flex h-full w-full bg-zinc-950 overflow-hidden">
      {/* Sidebar */}
      <div className="w-56 flex-shrink-0 border-r border-zinc-800 flex flex-col">
        <div className="px-4 py-3 flex items-center justify-between">
          <h3 className="text-base font-medium text-zinc-400">Enchant Sessions</h3>
          <button
            onClick={() => router.push("/enchant")}
            className="p-1 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded transition-colors"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>

        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto px-2 pb-4 enchant-scroll"
        >
          {sessions.map((session) => (
            <div
              key={session.id}
              className={`group flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer mb-1 transition-colors ${
                activeSessionId === session.id
                  ? "bg-zinc-800 text-zinc-100"
                  : "hover:bg-zinc-800/50 text-zinc-400"
              }`}
              onClick={() => {
                if (editingSessionId !== session.id) {
                  router.push(`/enchant/${session.id}`);
                }
              }}
            >
              <MessageSquare className="w-4 h-4 flex-shrink-0" />

              {editingSessionId === session.id ? (
                <div className="flex-1 flex items-center gap-1 min-w-0">
                  <input
                    type="text"
                    value={editingTitle}
                    onChange={(e) => setEditingTitle(e.target.value)}
                    className="w-20 flex-1 bg-zinc-700 text-zinc-100 text-sm px-2 py-1 rounded border border-zinc-600 focus:outline-none focus:border-zinc-500"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === "Enter") saveEditing();
                      if (e.key === "Escape") cancelEditing();
                    }}
                    onClick={(e) => e.stopPropagation()}
                  />
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      saveEditing();
                    }}
                    className="p-1 hover:bg-zinc-600 rounded flex-shrink-0"
                  >
                    <Check className="w-3.5 h-3.5 text-zinc-400 hover:text-zinc-200" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      cancelEditing();
                    }}
                    className="p-1 hover:bg-zinc-600 rounded flex-shrink-0"
                  >
                    <X className="w-3.5 h-3.5 text-zinc-400 hover:text-zinc-200" />
                  </button>
                </div>
              ) : (
                <>
                  <span className="flex-1 truncate text-sm">{session.title}</span>
                  <div className="hidden group-hover:flex items-center gap-1">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        startEditing(session);
                      }}
                      className="p-1 hover:bg-zinc-700 rounded"
                    >
                      <Pencil className="w-3.5 h-3.5 text-zinc-500 hover:text-zinc-300" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteSession(session.id);
                      }}
                      className="p-1 hover:bg-zinc-700 rounded"
                    >
                      <Trash2 className="w-3.5 h-3.5 text-zinc-500 hover:text-red-400" />
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}

          {sessions.length === 0 && initialLoaded && (
            <div className="text-center text-zinc-600 text-sm py-8">
              No sessions yet
            </div>
          )}

          {isLoadingMore && (
            <div className="flex justify-center py-4">
              <Loader2 className="w-5 h-5 animate-spin text-zinc-500" />
            </div>
          )}
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-h-0 min-w-0">
        {children}
      </div>
    </div>
  );
}

"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import { Trash2, MessageSquare, Pencil, Check, X, Loader2, Plus, Share2, Copy } from "lucide-react";
import { client } from "@/lib/api";
import { useLanguage } from "@/context/language-context";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import type { ExplorerSession, PagedExplorerSessionResponse } from "@/types/explore";

const PAGE_SIZE = 20;
const SERVER_ADDRESS = process.env.NEXT_PUBLIC_SERVER_ADDRESS;
const FRONT_END_PORT = process.env.NEXT_PUBLIC_FRONT_END_PORT;


interface ShareDialogProps {
  session: ExplorerSession;
  onClose: () => void;
  onShare: () => Promise<void>;
  onUnshare: () => Promise<void>;
}


function ShareDialog({ session, onClose, onShare, onUnshare }: ShareDialogProps) {
  const { t } = useLanguage();
  const [copied, setCopied] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const shareUrl = `${SERVER_ADDRESS}:${FRONT_END_PORT}/explorer/${session.id}`;

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isLoading) onClose();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [onClose, isLoading]);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleAction = async () => {
    setIsLoading(true);
    try {
      if (session.is_shared) {
        await onUnshare();
      } else {
        await onShare();
      }
      onClose();
    } catch {
      setIsLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm"
        onClick={() => !isLoading && onClose()}
      />

      <div className="relative bg-[#09090b] border border-zinc-800 rounded-xl shadow-2xl w-full max-w-md overflow-hidden">
        <div className="p-6">
          <h3 className="text-base font-semibold text-zinc-100 mb-3">
            {session.is_shared ? t("explorer.closeShare") : t("explorer.shareSession")}
          </h3>

          <div className="space-y-3">
            <p className="text-sm text-zinc-400">
              {session.is_shared
                ? t("explorer.sharedDesc")
                : t("explorer.shareDesc")}
            </p>
            <div className="flex items-center gap-2 bg-zinc-800/50 border border-zinc-700 rounded-lg px-3 py-2">
              <span className="flex-1 text-sm text-zinc-300 truncate">{shareUrl}</span>
              <button
                onClick={handleCopy}
                className={`p-1.5 rounded transition-colors flex-shrink-0 ${
                  copied
                    ? "text-green-400 bg-green-400/10"
                    : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700"
                }`}
              >
                {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              </button>
            </div>
          </div>
        </div>

        <div className="bg-zinc-900/50 px-6 py-4 flex items-center justify-end gap-3 border-t border-zinc-800/50">
          <button
            onClick={onClose}
            disabled={isLoading}
            className="px-4 py-2 rounded-lg text-sm text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors disabled:opacity-50"
          >
            {session.is_shared ? t("common.close") : t("common.cancel")}
          </button>
          <button
            onClick={handleAction}
            disabled={isLoading}
            className={`px-4 py-2 rounded-lg text-sm font-medium text-white shadow-lg transition-all flex items-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed ${
              session.is_shared
                ? "bg-red-600 hover:bg-red-500 border border-red-500/50"
                : "bg-blue-600 hover:bg-blue-500 border border-blue-500/50"
            }`}
          >
            {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
            {session.is_shared ? t("explorer.disableShare") : t("explorer.enableShare")}
          </button>
        </div>
      </div>
    </div>
  );
}


export default function ExplorerLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { t } = useLanguage();
  const [sessions, setSessions] = useState<ExplorerSession[]>([]);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [initialLoaded, setInitialLoaded] = useState(false);
  const [sharingSession, setSharingSession] = useState<ExplorerSession | null>(null);
  const [deletingSession, setDeletingSession] = useState<ExplorerSession | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const activeSessionId = pathname.startsWith("/explorer/")
    ? pathname.split("/")[2]
    : null;


  const fetchSessions = useCallback(async (skip: number = 0, append: boolean = false) => {
    try {
      const data: PagedExplorerSessionResponse = await client(
        `/api/explorer/sessions?skip=${skip}&limit=${PAGE_SIZE}`
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
    window.addEventListener("explorer-sessions-update", handleSessionsUpdate);
    return () => window.removeEventListener("explorer-sessions-update", handleSessionsUpdate);
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
      await client(`/api/explorer/sessions/${sessionId}`, { method: "DELETE" });
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (activeSessionId === sessionId) {
        router.push("/explorer");
      }
    } catch (error) {
      console.error("Failed to delete session:", error);
    }
  };


  const updateSessionTitle = async (sessionId: string, title: string) => {
    try {
      await client(`/api/explorer/sessions/${sessionId}`, {
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


  const startEditing = (session: ExplorerSession) => {
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


  const shareSession = async (sessionId: string) => {
    await client(`/api/explorer/sessions/${sessionId}/share`, { method: "POST" });
    setSessions((prev) =>
      prev.map((s) => (s.id === sessionId ? { ...s, is_shared: true } : s))
    );
  };


  const unshareSession = async (sessionId: string) => {
    await client(`/api/explorer/sessions/${sessionId}/unshare`, { method: "POST" });
    setSessions((prev) =>
      prev.map((s) => (s.id === sessionId ? { ...s, is_shared: false } : s))
    );
  };

  return (
    <div className="flex h-full w-full bg-zinc-950 overflow-hidden">
      {/* Sidebar */}
      <div className="w-56 flex-shrink-0 border-r border-zinc-800 flex flex-col">
        <div className="px-4 py-3 flex items-center justify-between">
          <h3 className="text-base font-medium text-zinc-400">{t("explorer.sessions")}</h3>
          <button
            onClick={() => router.push("/explorer")}
            className="p-1 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded transition-colors"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>

        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto px-2 pb-4 explorer-scroll"
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
                  router.push(`/explorer/${session.id}`);
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
                  {session.is_shared && (
                    <Share2 className="w-3 h-3 text-blue-400 flex-shrink-0 group-hover:hidden" />
                  )}
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
                        setDeletingSession(session);
                      }}
                      className="p-1 hover:bg-zinc-700 rounded"
                    >
                      <Trash2 className="w-3.5 h-3.5 text-zinc-500 hover:text-red-400" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setSharingSession(session);
                      }}
                      className="p-1 hover:bg-zinc-700 rounded"
                    >
                      <Share2 className={`w-3.5 h-3.5 ${session.is_shared ? "text-blue-400" : "text-zinc-500 hover:text-zinc-300"}`} />
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}

          {sessions.length === 0 && initialLoaded && (
            <div className="text-center text-zinc-600 text-sm py-8">
              {t("explorer.noSessions")}
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

      {/* Share Dialog */}
      {sharingSession && (
        <ShareDialog
          session={sharingSession}
          onClose={() => setSharingSession(null)}
          onShare={() => shareSession(sharingSession.id)}
          onUnshare={() => unshareSession(sharingSession.id)}
        />
      )}

      {/* Delete Confirm Dialog */}
      <ConfirmationDialog
        isOpen={!!deletingSession}
        onClose={() => setDeletingSession(null)}
        onConfirm={async () => {
          if (!deletingSession) return;
          setIsDeleting(true);
          await deleteSession(deletingSession.id);
          setIsDeleting(false);
          setDeletingSession(null);
        }}
        title={t("explorer.deleteSession")}
        description={
          <>
            <p>{t("explorer.deleteDesc")}</p>
            <p className="mt-2 text-zinc-500 truncate">{deletingSession?.title}</p>
          </>
        }
        confirmText={t("explorer.confirmDelete")}
        isLoading={isDeleting}
        variant="danger"
      />
    </div>
  );
}

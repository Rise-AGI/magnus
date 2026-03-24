// front_end/src/components/chat/group-invite-dialog.tsx
"use client";

import { useState, useEffect } from "react";
import { Users, Check, Loader2 } from "lucide-react";
import { client } from "@/lib/api";
import { Drawer } from "@/components/ui/drawer";
import { useLanguage } from "@/context/language-context";
import type { ConversationListItem } from "@/types/chat";

interface GroupInviteDialogProps {
  isOpen: boolean;
  onClose: () => void;
  targetUserId: string;
  targetUserName: string;
}

export function GroupInviteDialog({
  isOpen,
  onClose,
  targetUserId,
  targetUserName,
}: GroupInviteDialogProps) {
  const { t } = useLanguage();
  const [groups, setGroups] = useState<ConversationListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [inviting, setInviting] = useState<string | null>(null);
  const [done, setDone] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!isOpen) return;
    setDone(new Set());
    setErrors({});
    setLoading(true);
    client("/api/conversations?page_size=100")
      .then((res: { items: ConversationListItem[] }) => {
        setGroups(res.items.filter((c) => c.type === "group"));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [isOpen]);

  const handleInvite = async (groupId: string) => {
    if (inviting || done.has(groupId)) return;
    setInviting(groupId);
    setErrors((prev) => { const n = { ...prev }; delete n[groupId]; return n; });
    try {
      await client(`/api/conversations/${groupId}/members`, {
        json: { user_id: targetUserId },
      });
      setDone((prev) => new Set(prev).add(groupId));
    } catch (e: any) {
      const detail = e?.detail || t("chat.inviteFailed");
      const msg = detail === "User is already a member" ? t("chat.alreadyMember") : detail;
      setErrors((prev) => ({ ...prev, [groupId]: msg }));
    } finally {
      setInviting(null);
    }
  };

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title={t("chat.inviteToGroupTitle")}
      icon={<Users className="w-5 h-5 text-violet-400" />}
      width="w-[360px]"
    >
      <div className="space-y-3">
        <p className="text-xs text-zinc-500">
          {t("chat.inviteToGroupDesc").replace("{name}", targetUserName)}
        </p>

        {loading ? (
          <div className="flex items-center justify-center py-8 text-zinc-500 gap-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span className="text-sm">{t("common.loading")}</span>
          </div>
        ) : groups.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-zinc-600">
            <Users className="w-8 h-8 opacity-20 mb-2" />
            <span className="text-sm">{t("chat.noGroupConversations")}</span>
          </div>
        ) : (
          <div className="space-y-1.5">
            {groups.map((group) => {
              const isDone = done.has(group.id);
              const isLoading = inviting === group.id;
              const error = errors[group.id];

              return (
                <button
                  key={group.id}
                  onClick={() => handleInvite(group.id)}
                  disabled={!!inviting || isDone}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-colors text-left cursor-pointer
                    ${isDone
                      ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                      : error
                      ? "bg-red-500/10 border-red-500/30 text-red-400"
                      : "bg-zinc-900/60 border-zinc-800/50 hover:border-violet-500/40 hover:bg-violet-500/5 text-zinc-200"
                    }
                    disabled:cursor-not-allowed
                  `}
                >
                  <div className="w-8 h-8 rounded-full bg-violet-500/20 border border-violet-500/30 flex items-center justify-center flex-shrink-0">
                    <Users className="w-3.5 h-3.5 text-violet-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">
                      {group.name || t("chat.type.group")}
                    </div>
                    <div className="text-[10px] text-zinc-600">
                      {group.member_count} {t("chat.members")}
                    </div>
                  </div>
                  <div className="flex-shrink-0">
                    {isLoading ? (
                      <Loader2 className="w-4 h-4 animate-spin text-violet-400" />
                    ) : isDone ? (
                      <Check className="w-4 h-4 text-emerald-400" />
                    ) : error ? (
                      <span className="text-[10px] text-red-400">{error}</span>
                    ) : null}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </Drawer>
  );
}

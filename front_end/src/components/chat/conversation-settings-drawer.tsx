"use client";

import { useState, useEffect } from "react";
import { Settings, X, Shield } from "lucide-react";
import { client } from "@/lib/api";
import { Drawer } from "@/components/ui/drawer";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { useLanguage } from "@/context/language-context";
import { useAuth } from "@/context/auth-context";
import type { ConversationDetail, ConversationMember } from "@/types/chat";

interface UserOption {
  label: string;
  value: string;
  meta?: string;
  icon?: string;
  initials?: string;
}

interface ConversationSettingsDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  conversation: ConversationDetail;
  onUpdate: (conv: ConversationDetail) => void;
}

const AVATAR_COLORS = [
  "bg-blue-500", "bg-violet-500", "bg-emerald-500", "bg-amber-500",
  "bg-rose-500", "bg-cyan-500", "bg-orange-500", "bg-teal-500",
];

function getAvatarColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) & 0xffffffff;
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}


export function ConversationSettingsDrawer({
  isOpen,
  onClose,
  conversation,
  onUpdate,
}: ConversationSettingsDrawerProps) {
  const { t } = useLanguage();
  const { user: currentUser } = useAuth();

  const [userOptions, setUserOptions] = useState<UserOption[]>([]);
  const [addMemberValue, setAddMemberValue] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [removingMember, setRemovingMember] = useState<ConversationMember | null>(null);
  const [isRemoving, setIsRemoving] = useState(false);

  const isOwner = conversation.created_by === currentUser?.id;

  useEffect(() => {
    if (!isOpen) return;
    client("/api/users").then((users: any[]) => {
      const memberIds = new Set(conversation.members.map((m) => m.user_id));
      setUserOptions(
        users
          .filter((u) => !memberIds.has(u.id))
          .map((u) => ({
            label: u.name,
            value: u.id,
            meta: u.email || undefined,
            icon: u.avatar_url || undefined,
            initials: u.name.substring(0, 1).toUpperCase(),
          }))
      );
    }).catch(() => {});
  }, [isOpen, conversation.members]);

  const handleAddMember = async (userId: string) => {
    if (!userId || isAdding) return;
    setIsAdding(true);
    try {
      const newMember = await client(`/api/conversations/${conversation.id}/members`, {
        json: { user_id: userId },
      });
      onUpdate({
        ...conversation,
        members: [...conversation.members, newMember],
      });
      setAddMemberValue("");
      setUserOptions((prev) => prev.filter((o) => o.value !== userId));
    } catch (e) {
      console.error("Failed to add member:", e);
    } finally {
      setIsAdding(false);
    }
  };

  const handleRemoveMember = async () => {
    if (!removingMember) return;
    setIsRemoving(true);
    try {
      await client(`/api/conversations/${conversation.id}/members/${removingMember.user_id}`, {
        method: "DELETE",
      });
      const removed = removingMember;
      onUpdate({
        ...conversation,
        members: conversation.members.filter((m) => m.user_id !== removed.user_id),
      });
      if (removed.user) {
        setUserOptions((prev) => [
          ...prev,
          {
            label: removed.user!.name,
            value: removed.user_id,
            icon: removed.user!.avatar_url || undefined,
            initials: removed.user!.name.substring(0, 1).toUpperCase(),
          },
        ]);
      }
    } catch (e) {
      console.error("Failed to remove member:", e);
    } finally {
      setIsRemoving(false);
      setRemovingMember(null);
    }
  };

  return (
    <>
      <Drawer
        isOpen={isOpen}
        onClose={onClose}
        title={t("chat.settings")}
        icon={<Settings className="w-5 h-5 text-blue-500" />}
        width="w-[380px]"
      >
        <div className="space-y-6">
          {/* Add member (owner only) */}
          {isOwner && (
            <div>
              <label className="text-[10px] uppercase tracking-widest mb-1.5 block font-semibold text-zinc-600">
                {t("chat.addMember")}
              </label>
              <SearchableSelect
                value={addMemberValue}
                options={userOptions}
                onChange={handleAddMember}
                placeholder={t("common.search")}
                disabled={isAdding}
              />
            </div>
          )}

          {/* Member list */}
          <div>
            <label className="text-[10px] uppercase tracking-widest mb-2 block font-semibold text-zinc-600">
              {t("chat.members")} ({conversation.members.length})
            </label>
            <div className="space-y-1">
              {conversation.members.map((member) => {
                const name = member.user?.name || "?";
                const colorClass = getAvatarColor(member.user_id);
                const canRemove = isOwner && member.role !== "owner";

                return (
                  <div
                    key={member.user_id}
                    className="flex items-center gap-3 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-800/50 group hover:border-zinc-700/50 transition-colors"
                  >
                    {/* Avatar */}
                    <div className={`w-8 h-8 rounded-full flex-shrink-0 overflow-hidden flex items-center justify-center ${member.user?.avatar_url ? "" : colorClass}`}>
                      {member.user?.avatar_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={member.user.avatar_url}
                          alt={name}
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <span className="text-xs font-bold text-white">
                          {name.charAt(0).toUpperCase()}
                        </span>
                      )}
                    </div>

                    {/* Name */}
                    <div className="flex-1 min-w-0">
                      <span className="text-sm text-zinc-200 truncate block font-medium">
                        {name}
                      </span>
                      {member.user?.email && (
                        <span className="text-[10px] text-zinc-600 truncate block">
                          {member.user.email}
                        </span>
                      )}
                    </div>

                    {/* Owner badge */}
                    {member.role === "owner" && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-900/30 text-amber-400 border border-amber-800/40">
                        <Shield className="w-2.5 h-2.5" />
                        {t("chat.owner")}
                      </span>
                    )}

                    {/* Remove button */}
                    {canRemove && (
                      <button
                        onClick={() => setRemovingMember(member)}
                        className="hidden group-hover:flex items-center justify-center p-1 text-zinc-600 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors flex-shrink-0 cursor-pointer"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </Drawer>

      {/* Remove member confirmation */}
      <ConfirmationDialog
        isOpen={!!removingMember}
        onClose={() => setRemovingMember(null)}
        onConfirm={handleRemoveMember}
        title={t("chat.removeMember")}
        description={t("chat.removeMemberConfirm")}
        confirmText={t("chat.removeMember")}
        isLoading={isRemoving}
        variant="danger"
      />
    </>
  );
}

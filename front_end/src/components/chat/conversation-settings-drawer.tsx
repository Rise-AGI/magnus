"use client";

import { useState, useEffect, useRef } from "react";
import { Settings, X, Shield, Check, Pencil } from "lucide-react";
import { client } from "@/lib/api";
import { Drawer } from "@/components/ui/drawer";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { useLanguage } from "@/context/language-context";
import { useAuth } from "@/context/auth-context";
import { AvatarCircle } from "@/components/ui/user-avatar";
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
  const [editingName, setEditingName] = useState(false);
  const [nameValue, setNameValue] = useState(conversation.name || "");
  const [isSavingName, setIsSavingName] = useState(false);
  const nameInputRef = useRef<HTMLInputElement>(null);

  const isOwner = conversation.created_by === currentUser?.id;
  const isGroup = conversation.type === "group";

  useEffect(() => {
    setNameValue(conversation.name || "");
  }, [conversation.name]);

  useEffect(() => {
    if (editingName) nameInputRef.current?.focus();
  }, [editingName]);

  const handleSaveName = async () => {
    const trimmed = nameValue.trim();
    if (!trimmed || trimmed === conversation.name || isSavingName) return;
    setIsSavingName(true);
    try {
      const updated = await client(`/api/conversations/${conversation.id}`, {
        method: "PATCH",
        json: { name: trimmed },
      });
      onUpdate({ ...conversation, name: updated.name });
      setEditingName(false);
    } catch (e) {
      console.error("Failed to rename:", e);
    } finally {
      setIsSavingName(false);
    }
  };

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
            initials: u.name.substring(0, 2).toUpperCase(),
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
            initials: removed.user!.name.substring(0, 2).toUpperCase(),
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
          {/* Group name (owner only) */}
          {isOwner && isGroup && (
            <div>
              <label className="text-[10px] uppercase tracking-widest mb-1.5 block font-semibold text-zinc-600">
                {t("chat.renameGroup")}
              </label>
              <div className="flex items-center gap-2">
                {editingName ? (
                  <>
                    <input
                      ref={nameInputRef}
                      value={nameValue}
                      onChange={(e) => setNameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleSaveName();
                        if (e.key === "Escape") { setEditingName(false); setNameValue(conversation.name || ""); }
                      }}
                      placeholder={t("chat.groupNamePlaceholder")}
                      className="flex-1 bg-zinc-950 border border-blue-500 ring-1 ring-blue-500/20 px-3 py-2 rounded-lg text-sm text-white outline-none"
                    />
                    <button
                      onClick={handleSaveName}
                      disabled={isSavingName || !nameValue.trim()}
                      className="p-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors disabled:opacity-50 cursor-pointer"
                    >
                      <Check className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => { setEditingName(false); setNameValue(conversation.name || ""); }}
                      className="p-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 rounded-lg transition-colors cursor-pointer"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </>
                ) : (
                  <button
                    onClick={() => setEditingName(true)}
                    className="flex-1 flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-800/50 hover:border-zinc-700 transition-colors group cursor-pointer"
                  >
                    <span className="text-sm text-zinc-200 truncate">
                      {conversation.name || <span className="text-zinc-600 italic">{t("chat.groupNamePlaceholder")}</span>}
                    </span>
                    <Pencil className="w-3.5 h-3.5 text-zinc-600 group-hover:text-zinc-400 flex-shrink-0 transition-colors" />
                  </button>
                )}
              </div>
            </div>
          )}

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
                const canRemove = isOwner && member.role !== "owner";

                return (
                  <div
                    key={member.user_id}
                    className="flex items-center gap-3 px-3 py-2 rounded-lg bg-zinc-900/60 border border-zinc-800/50 group hover:border-zinc-700/50 transition-colors"
                  >
                    <AvatarCircle user={member.user ? { name, avatar_url: member.user.avatar_url } : null} size="sm" />

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

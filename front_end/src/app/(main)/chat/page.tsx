"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { MessageCircle, Users, X, Loader2 } from "lucide-react";
import { client } from "@/lib/api";
import { useLanguage } from "@/context/language-context";
import { useAuth } from "@/context/auth-context";
import { SearchableSelect } from "@/components/ui/searchable-select";
import type { ConversationType } from "@/types/chat";

interface UserOption {
  label: string;
  value: string;
  meta?: string;
  icon?: string;
  initials?: string;
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


export default function ChatPage() {
  const router = useRouter();
  const { t } = useLanguage();
  const { user: currentUser } = useAuth();

  const [type, setType] = useState<ConversationType>("p2p");
  const [groupName, setGroupName] = useState("");
  const [selectedMembers, setSelectedMembers] = useState<UserOption[]>([]);
  const [searchValue, setSearchValue] = useState("");
  const [userOptions, setUserOptions] = useState<UserOption[]>([]);
  const [isCreating, setIsCreating] = useState(false);

  useEffect(() => {
    client("/api/users").then((users: any[]) => {
      setUserOptions(
        users
          .filter((u) => u.id !== currentUser?.id)
          .map((u) => ({
            label: u.name,
            value: u.id,
            meta: u.email || undefined,
            icon: u.avatar_url || undefined,
            initials: u.name.substring(0, 1).toUpperCase(),
          }))
      );
    }).catch(() => {});
  }, [currentUser?.id]);

  const handleSelectMember = (userId: string) => {
    if (!userId) return;
    const opt = userOptions.find((o) => o.value === userId);
    if (!opt) return;

    if (type === "p2p") {
      setSelectedMembers([opt]);
      setSearchValue("");
      return;
    }

    if (selectedMembers.find((m) => m.value === userId)) return;
    setSelectedMembers((prev) => [...prev, opt]);
    setSearchValue("");
  };

  const handleRemoveMember = (userId: string) => {
    setSelectedMembers((prev) => prev.filter((m) => m.value !== userId));
  };

  const handleCreate = async () => {
    if (selectedMembers.length === 0) return;
    setIsCreating(true);
    try {
      const body: { type: ConversationType; member_ids: string[]; name?: string } = {
        type,
        member_ids: selectedMembers.map((m) => m.value),
      };
      if (type === "group" && groupName.trim()) {
        body.name = groupName.trim();
      }
      const conv = await client("/api/conversations", { json: body });
      window.dispatchEvent(new Event("chat-conversations-update"));
      router.push(`/chat/${conv.id}`);
    } catch (e) {
      console.error("Failed to create conversation:", e);
    } finally {
      setIsCreating(false);
    }
  };

  const availableOptions = userOptions.filter(
    (o) => !selectedMembers.find((m) => m.value === o.value)
  );

  const canCreate = selectedMembers.length > 0 && (type === "p2p" || type === "group");

  return (
    <div className="flex-1 flex items-center justify-center p-6">
      <div className="w-full max-w-sm space-y-5">
        {/* Icon + title */}
        <div className="text-center space-y-2">
          <div className="w-12 h-12 rounded-2xl bg-zinc-900 border border-zinc-800 flex items-center justify-center mx-auto">
            <MessageCircle className="w-5 h-5 text-zinc-500" />
          </div>
          <h2 className="text-base font-semibold text-zinc-200">{t("chat.newConversation")}</h2>
        </div>

        {/* Type toggle */}
        <div className="flex gap-1.5 bg-zinc-900/70 rounded-lg p-1 border border-zinc-800/70">
          <button
            onClick={() => { setType("p2p"); setSelectedMembers([]); }}
            className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-md text-sm font-medium transition-all cursor-pointer ${
              type === "p2p"
                ? "bg-zinc-700/80 text-zinc-100 shadow-sm"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
          >
            <MessageCircle className="w-3.5 h-3.5" />
            {t("chat.type.p2p")}
          </button>
          <button
            onClick={() => { setType("group"); setSelectedMembers([]); }}
            className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-md text-sm font-medium transition-all cursor-pointer ${
              type === "group"
                ? "bg-zinc-700/80 text-zinc-100 shadow-sm"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
          >
            <Users className="w-3.5 h-3.5" />
            {t("chat.type.group")}
          </button>
        </div>

        {/* Group name input */}
        {type === "group" && (
          <div>
            <label className="text-[10px] uppercase tracking-widest mb-1.5 block font-semibold text-zinc-600">
              {t("chat.conversationName")}
            </label>
            <input
              value={groupName}
              onChange={(e) => setGroupName(e.target.value)}
              placeholder={t("chat.conversationName")}
              className="w-full px-3.5 py-2.5 bg-zinc-900/80 border border-zinc-800 rounded-xl text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-600 focus:ring-1 focus:ring-zinc-700/50 transition-colors"
            />
          </div>
        )}

        {/* Member select */}
        <div>
          <label className="text-[10px] uppercase tracking-widest mb-1.5 block font-semibold text-zinc-600">
            {t("chat.selectMembers")}
          </label>
          <SearchableSelect
            value={searchValue}
            options={type === "p2p" ? userOptions : availableOptions}
            onChange={handleSelectMember}
            placeholder={t("common.search")}
          />
        </div>

        {/* Selected members chips */}
        {selectedMembers.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {selectedMembers.map((member) => {
              const colorClass = getAvatarColor(member.value);
              return (
                <div
                  key={member.value}
                  className="flex items-center gap-1.5 bg-zinc-800/80 border border-zinc-700/50 rounded-full pl-1 pr-2 py-1"
                >
                  {member.icon ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={member.icon} alt="" className="w-4 h-4 rounded-full object-cover" />
                  ) : (
                    <div className={`w-4 h-4 rounded-full ${colorClass} flex items-center justify-center text-[8px] font-bold text-white`}>
                      {member.initials}
                    </div>
                  )}
                  <span className="text-xs text-zinc-300">{member.label}</span>
                  {type === "group" && (
                    <button
                      onClick={() => handleRemoveMember(member.value)}
                      className="p-0.5 text-zinc-600 hover:text-zinc-300 transition-colors cursor-pointer"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Create button */}
        <button
          onClick={handleCreate}
          disabled={isCreating || !canCreate}
          className="w-full px-4 py-2.5 rounded-xl text-sm font-semibold bg-blue-600 hover:bg-blue-500 text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2 cursor-pointer shadow-sm"
        >
          {isCreating && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {t("chat.create")}
        </button>
      </div>
    </div>
  );
}

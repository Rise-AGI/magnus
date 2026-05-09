// front_end/src/components/ui/transferable-author.tsx
"use client";

import { useState, useRef } from "react";
import { ArrowRight, Loader2, ChevronDown } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { client } from "@/lib/api";
import { useLanguage } from "@/context/language-context";
import { ConfirmationDialog } from "./confirmation-dialog";
import { AvatarCircle } from "./user-avatar";
import { PersonHoverCard } from "./person-hover-card";
import type { User } from "@/types/auth";


interface TransferableAuthorProps {
  user: User;
  label?: string;
  subText?: React.ReactNode;
  canTransfer: boolean;
  entityType: "blueprints" | "skills" | "services" | "images";
  entityId: string;
  entityTitle: string;
  onTransferred: (newOwner: User) => void;
  avatarSize?: "sm" | "md";
}


/**
 * Author 展示控件，承担两种相互正交的语义：
 *
 * - **左键主体（avatar + label + name）**：开 PersonHoverCard 看这个人的资料 / 跳转其作品。
 *   这是面向所有人的高频路径，无关 ownership。
 * - **右侧 caret 按钮**：开"转让 owner" dropdown。仅 canTransfer=true 时渲染，是 owner-only 的低频特权操作。
 *
 * 这样把"看人"和"转让"拆开，非 owner 用户第一次有了点击响应；owner 也获得"看自己资料"的入口。
 */
export function TransferableAuthor({
  user,
  label,
  subText,
  canTransfer,
  entityType,
  entityId,
  entityTitle,
  onTransferred,
  avatarSize = "md",
}: TransferableAuthorProps) {
  const [candidates, setCandidates] = useState<User[]>([]);
  const [search, setSearch] = useState("");
  const [isTransferring, setIsTransferring] = useState(false);
  const [pendingTarget, setPendingTarget] = useState<User | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const { t } = useLanguage();

  const handleOpenChange = (open: boolean) => {
    if (open) {
      client("/api/users/transfer-candidates").then(setCandidates);
      setTimeout(() => searchRef.current?.focus(), 50);
    } else {
      setSearch("");
    }
  };

  const handleTransfer = async (targetId: string) => {
    setIsTransferring(true);
    try {
      const result = await client(`/api/${entityType}/${entityId}/transfer`, {
        json: { new_owner_id: targetId },
      });
      const newOwner = result.user || result.owner;
      if (newOwner) onTransferred(newOwner);
    } catch (error) {
      console.error("Transfer failed:", error);
    } finally {
      setIsTransferring(false);
      setPendingTarget(null);
    }
  };

  const nameClass = avatarSize === "sm"
    ? "text-sm font-medium text-zinc-200"
    : "text-base font-bold tracking-wide text-zinc-200";

  const filtered = candidates.filter(
    (c) => c.id !== user.id && c.name.toLowerCase().includes(search.toLowerCase()),
  );

  // 主体（avatar + 名字 + 副文）— 一律走 PersonHoverCard
  const mainBlock = (
    <PersonHoverCard
      userId={user.id}
      warm={{ name: user.name, avatar_url: user.avatar_url ?? null }}
    >
      <div className="flex items-center gap-3 cursor-pointer rounded-lg -m-1.5 p-1.5 hover:bg-zinc-800/50 transition-colors">
        <AvatarCircle user={user} size={avatarSize} />
        <div className="flex flex-col text-left">
          {label && (
            <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">
              {label}
            </span>
          )}
          <span className={nameClass}>{user.name}</span>
          {subText && (
            <span className="text-xs text-zinc-500 font-mono tracking-tight leading-none mt-0.5">
              {subText}
            </span>
          )}
        </div>
      </div>
    </PersonHoverCard>
  );

  if (!canTransfer) {
    return mainBlock;
  }

  return (
    <>
      <div className="flex items-center gap-1">
        {mainBlock}

        <DropdownMenu.Root onOpenChange={handleOpenChange}>
          <DropdownMenu.Trigger asChild>
            <button
              type="button"
              disabled={isTransferring}
              onClick={(e) => e.stopPropagation()}
              title={t("personCard.transferOwner")}
              className="p-1 rounded-md text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors focus:outline-none data-[state=open]:bg-zinc-800 data-[state=open]:text-zinc-200"
            >
              {isTransferring ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <ChevronDown className="w-3.5 h-3.5" />
              )}
            </button>
          </DropdownMenu.Trigger>

          <DropdownMenu.Portal>
            <DropdownMenu.Content
              sideOffset={8}
              align="start"
              className="w-56 bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl z-[200] overflow-hidden animate-in fade-in slide-in-from-top-2 duration-150"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wider text-zinc-500 font-bold">
                {t("personCard.transferOwner")}
              </div>
              <div className="p-2 pt-1">
                <input
                  ref={searchRef}
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="..."
                  className="w-full px-3 py-1.5 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-zinc-600"
                  onKeyDown={(e) => e.stopPropagation()}
                />
              </div>
              <div className="max-h-48 overflow-y-auto">
                {filtered.length === 0 && (
                  <div className="px-3 py-4 text-xs text-zinc-600 text-center">—</div>
                )}
                {filtered.map((c) => (
                  <DropdownMenu.Item
                    key={c.id}
                    disabled={isTransferring}
                    onSelect={() => setPendingTarget(c)}
                    className="w-full flex items-center gap-3 px-3 py-2 hover:bg-zinc-800 transition-colors text-left disabled:opacity-50 cursor-pointer focus:outline-none focus:bg-zinc-800"
                  >
                    <AvatarCircle user={c} size="xs" />
                    <span className="text-sm text-zinc-300 truncate">{c.name}</span>
                    {isTransferring && <Loader2 className="w-3.5 h-3.5 animate-spin text-zinc-500 ml-auto" />}
                  </DropdownMenu.Item>
                ))}
              </div>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>

      <ConfirmationDialog
        isOpen={pendingTarget !== null}
        onClose={() => setPendingTarget(null)}
        onConfirm={() => {
          if (pendingTarget) handleTransfer(pendingTarget.id);
        }}
        title={t("common.transferTitle")}
        description={
          pendingTarget && (
            <>
              <span>{t("common.transferDesc", { type: t(`nav.${entityType}`), title: entityTitle })}</span>
              <div className="flex items-center justify-center gap-4 py-4">
                <div className="flex flex-col items-center gap-1.5 min-w-0">
                  <AvatarCircle user={user} size="md" />
                  <span className="text-sm font-medium text-zinc-300 truncate max-w-[100px]">{user.name}</span>
                </div>
                <ArrowRight className="w-5 h-5 text-zinc-500 flex-shrink-0" />
                <div className="flex flex-col items-center gap-1.5 min-w-0">
                  <AvatarCircle user={pendingTarget} size="md" />
                  <span className="text-sm font-medium text-zinc-200 truncate max-w-[100px]">{pendingTarget.name}</span>
                </div>
              </div>
            </>
          )
        }
        variant="default"
        confirmText={t("common.transfer")}
        confirmInput={entityId}
        isLoading={isTransferring}
      />
    </>
  );
}

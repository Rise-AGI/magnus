// front_end/src/components/ui/transferable-author.tsx
"use client";

import { useState, useRef } from "react";
import { ArrowRight, Loader2 } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { client } from "@/lib/api";
import { useLanguage } from "@/context/language-context";
import { ConfirmationDialog } from "./confirmation-dialog";
import { AvatarCircle } from "./user-avatar";
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


export function TransferableAuthor({
  user, label, subText, canTransfer, entityType, entityId, entityTitle, onTransferred, avatarSize = "md",
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

  // 不可转让时，直接渲染静态 avatar，不挂 DropdownMenu
  if (!canTransfer) {
    return (
      <div className="flex items-center gap-3">
        <AvatarCircle user={user} size={avatarSize} />
        <div className="flex flex-col">
          {label && <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">{label}</span>}
          <span className={nameClass}>{user.name}</span>
          {subText && <span className="text-xs text-zinc-500 font-mono tracking-tight leading-none mt-0.5">{subText}</span>}
        </div>
      </div>
    );
  }

  return (
    <>
    <DropdownMenu.Root onOpenChange={handleOpenChange}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          disabled={isTransferring}
          onClick={(e) => e.stopPropagation()}
          className="flex items-center gap-3 cursor-pointer rounded-lg -m-1.5 p-1.5 hover:bg-zinc-800/50 transition-colors focus:outline-none"
        >
          <AvatarCircle user={user} size={avatarSize} />
          <div className="flex flex-col text-left">
            {label && <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">{label}</span>}
            <span className={nameClass}>{user.name}</span>
            {subText && <span className="text-xs text-zinc-500 font-mono tracking-tight leading-none mt-0.5">{subText}</span>}
          </div>
        </button>
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal>
        <DropdownMenu.Content
          sideOffset={8}
          align="start"
          className="w-56 bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl z-[200] overflow-hidden animate-in fade-in slide-in-from-top-2 duration-150"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="p-2">
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

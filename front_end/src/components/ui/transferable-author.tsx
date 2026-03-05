// front_end/src/components/ui/transferable-author.tsx
"use client";

import { useState, useRef } from "react";
import { Loader2 } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { client } from "@/lib/api";
import type { User } from "@/types/auth";


interface TransferableAuthorProps {
  user: User;
  label?: string;
  subText?: React.ReactNode;
  canTransfer: boolean;
  entityType: "blueprints" | "skills" | "services" | "images";
  entityId: string;
  onTransferred: (newOwner: User) => void;
  avatarSize?: "sm" | "md";
}


function Avatar({ user, size }: { user: User; size: string }) {
  if (user.avatar_url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={user.avatar_url}
        alt={user.name}
        className={`${size} rounded-full border border-zinc-700/50 object-cover shadow-sm`}
      />
    );
  }
  return (
    <div className={`${size} rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-xs font-bold border border-indigo-500/30`}>
      {user.name.substring(0, 2).toUpperCase()}
    </div>
  );
}


export function TransferableAuthor({
  user, label, subText, canTransfer, entityType, entityId, onTransferred, avatarSize = "md",
}: TransferableAuthorProps) {
  const [candidates, setCandidates] = useState<User[]>([]);
  const [search, setSearch] = useState("");
  const [isTransferring, setIsTransferring] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

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
    }
  };

  const size = avatarSize === "sm" ? "w-8 h-8" : "w-10 h-10";
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
        <Avatar user={user} size={size} />
        <div className="flex flex-col">
          {label && <span className="text-xs text-zinc-500 uppercase font-bold tracking-wider mb-0.5">{label}</span>}
          <span className={nameClass}>{user.name}</span>
          {subText && <span className="text-xs text-zinc-500 font-mono tracking-tight leading-none mt-0.5">{subText}</span>}
        </div>
      </div>
    );
  }

  return (
    <DropdownMenu.Root onOpenChange={handleOpenChange}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          disabled={isTransferring}
          onClick={(e) => e.stopPropagation()}
          className="flex items-center gap-3 cursor-pointer rounded-lg -m-1.5 p-1.5 hover:bg-zinc-800/50 transition-colors focus:outline-none"
        >
          <Avatar user={user} size={size} />
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
                onSelect={() => handleTransfer(c.id)}
                className="w-full flex items-center gap-3 px-3 py-2 hover:bg-zinc-800 transition-colors text-left disabled:opacity-50 cursor-pointer focus:outline-none focus:bg-zinc-800"
              >
                <Avatar user={c} size="w-7 h-7" />
                <span className="text-sm text-zinc-300 truncate">{c.name}</span>
                {isTransferring && <Loader2 className="w-3.5 h-3.5 animate-spin text-zinc-500 ml-auto" />}
              </DropdownMenu.Item>
            ))}
          </div>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

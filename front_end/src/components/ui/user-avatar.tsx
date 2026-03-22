// front_end/src/components/ui/user-avatar.tsx
"use client";

import { useState } from "react";
import { User as UserIcon, UserX } from "lucide-react";
import type { User } from "@/types/auth";

type AvatarSize = "xs" | "sm" | "md" | "lg";

const SIZE_CONFIG: Record<AvatarSize, { dim: string; text: string; icon: string }> = {
  xs: { dim: "w-7 h-7", text: "text-[10px]", icon: "w-3 h-3" },
  sm: { dim: "w-8 h-8", text: "text-xs", icon: "w-4 h-4" },
  md: { dim: "w-10 h-10", text: "text-sm", icon: "w-5 h-5" },
  lg: { dim: "w-16 h-16", text: "text-xl", icon: "w-7 h-7" },
};

interface AvatarCircleProps {
  user?: { name: string; avatar_url?: string | null } | null;
  size?: AvatarSize;
  className?: string;
}

export function AvatarCircle({ user, size = "sm", className = "" }: AvatarCircleProps) {
  const [broken, setBroken] = useState(false);
  const { dim, text, icon } = SIZE_CONFIG[size];

  if (!user) {
    return (
      <div className={`${dim} rounded-full bg-zinc-800 flex items-center justify-center border border-zinc-700 flex-shrink-0 ${className}`}>
        <UserIcon className={`${icon} text-zinc-500`} />
      </div>
    );
  }

  const hasImage = user.avatar_url && !broken;

  return (
    <div className={`${dim} rounded-full flex-shrink-0 overflow-hidden flex items-center justify-center ${
      hasImage
        ? "border border-zinc-700/50 shadow-sm"
        : broken
        ? "bg-zinc-800 border border-zinc-700/50"
        : "bg-indigo-500/20 border border-indigo-500/30"
    } ${className}`}>
      {hasImage ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={user.avatar_url!}
          alt={user.name}
          className="w-full h-full object-cover"
          onError={() => setBroken(true)}
        />
      ) : broken ? (
        <UserX className={`${icon} text-zinc-600`} />
      ) : (
        <span className={`${text} font-bold text-indigo-400`}>
          {user.name.substring(0, 2).toUpperCase()}
        </span>
      )}
    </div>
  );
}

interface UserAvatarProps {
  user?: User;
  subText?: React.ReactNode;
  size?: AvatarSize;
}

export function UserAvatar({ user, subText, size = "sm" }: UserAvatarProps) {
  if (!user) {
    return <AvatarCircle size={size} />;
  }

  return (
    <div className="flex items-center gap-3">
      <AvatarCircle user={user} size={size} />
      <div className="flex flex-col gap-0.5">
        <span className="text-sm font-medium text-zinc-200 leading-none">{user.name}</span>
        {subText && (
          <span className="text-xs text-zinc-500 font-mono tracking-tight leading-none">{subText}</span>
        )}
      </div>
    </div>
  );
}

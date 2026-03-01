// front_end/src/components/ui/user-avatar.tsx
import { User } from "@/types/auth";
import { User as UserIcon } from "lucide-react";

interface UserAvatarProps {
  user?: User;
  subText?: React.ReactNode;
}

export function UserAvatar({ user, subText }: UserAvatarProps) {
  if (!user) {
    return (
      <div className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center border border-zinc-700">
         <UserIcon className="w-4 h-4 text-zinc-500" />
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3">
      {user.avatar_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img 
          src={user.avatar_url} 
          alt={user.name} 
          className="w-8 h-8 rounded-full border border-zinc-700/50 object-cover shadow-sm flex-shrink-0"
        />
      ) : (
        <div className="w-8 h-8 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-xs font-bold border border-indigo-500/30 flex-shrink-0">
          {user.name.substring(0, 2).toUpperCase()}
        </div>
      )}
      
      <div className="flex flex-col gap-0.5">
        <span className="text-sm font-medium text-zinc-200 leading-none">{user.name}</span>
        {subText && (
           <span className="text-xs text-zinc-500 font-mono tracking-tight leading-none">{subText}</span>
        )}
      </div>
    </div>
  );
}
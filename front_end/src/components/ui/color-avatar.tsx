// front_end/src/components/ui/color-avatar.tsx

export const AVATAR_COLORS = [
  "bg-blue-500", "bg-violet-500", "bg-emerald-500", "bg-amber-500",
  "bg-rose-500", "bg-cyan-500", "bg-orange-500", "bg-teal-500",
];

export function getAvatarColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) & 0xffffffff;
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

export function ColorAvatar({ name, avatarUrl, userId, size = "sm" }: {
  name?: string;
  avatarUrl?: string | null;
  userId?: string;
  size?: "xs" | "sm" | "md";
}) {
  const dim = size === "xs" ? "w-4 h-4 text-[8px]" : size === "sm" ? "w-7 h-7 text-xs" : "w-9 h-9 text-sm";

  if (avatarUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={avatarUrl}
        alt={name || ""}
        className={`${dim} rounded-full object-cover flex-shrink-0${size !== "xs" ? " border border-zinc-700/40" : ""}`}
      />
    );
  }

  const colorClass = getAvatarColor(userId || name || "");
  return (
    <div className={`${dim} rounded-full ${colorClass} flex items-center justify-center flex-shrink-0 font-semibold text-white`}>
      {(name || "?").charAt(0).toUpperCase()}
    </div>
  );
}

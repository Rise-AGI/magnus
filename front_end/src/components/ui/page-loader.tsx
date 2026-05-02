// front_end/src/components/ui/page-loader.tsx
"use client";

import { cn } from "@/lib/utils";

type Size = "sm" | "md" | "lg";
type Tone = "blue" | "teal" | "neutral";
type Variant = "plain" | "card";

interface PageLoaderProps {
  label?: string;
  size?: Size;
  tone?: Tone;
  variant?: Variant;
  /** 让外层容器充满父布局（h-full）。默认仅居中，由父级决定高度。 */
  fullHeight?: boolean;
  className?: string;
}

interface SizeConfig {
  box: string;
  ring: string;
  dot: string;
  halo: string;
  text: string;
  gap: string;
}

interface ToneConfig {
  ring: string;
  dot: string;
  halo: string;
  text: string;
}

const SIZE: Record<Size, SizeConfig> = {
  sm: {
    box: "w-5 h-5",
    ring: "border-[1.5px]",
    dot: "h-1 w-1",
    halo: "-inset-1",
    text: "text-xs",
    gap: "gap-2",
  },
  md: {
    box: "w-8 h-8",
    ring: "border-2",
    dot: "h-1.5 w-1.5",
    halo: "-inset-1.5",
    text: "text-sm",
    gap: "gap-3",
  },
  lg: {
    box: "w-10 h-10",
    ring: "border-2",
    dot: "h-2 w-2",
    halo: "-inset-2",
    text: "text-sm",
    gap: "gap-4",
  },
};

const TONE: Record<Tone, ToneConfig> = {
  blue: {
    ring: "border-t-blue-500/90 border-r-blue-500/35",
    dot: "bg-blue-400/80",
    halo: "bg-blue-500/15",
    text: "text-zinc-400",
  },
  teal: {
    ring: "border-t-teal-500/90 border-r-teal-500/35",
    dot: "bg-teal-400/80",
    halo: "bg-teal-500/15",
    text: "text-zinc-400",
  },
  neutral: {
    ring: "border-t-zinc-300/90 border-r-zinc-400/30",
    dot: "bg-zinc-400/70",
    halo: "bg-zinc-400/10",
    text: "text-zinc-500",
  },
};

export function PageLoader({
  label,
  size = "md",
  tone = "blue",
  variant = "plain",
  fullHeight = false,
  className,
}: PageLoaderProps) {
  const s = SIZE[size];
  const t = TONE[tone];

  const inner = (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center select-none",
        // 延迟淡入：120ms 内完成的加载完全无 flash（见 globals.css）
        "magnus-loader-in",
        s.gap,
      )}
      role="status"
      aria-live="polite"
    >
      <div className={cn("relative inline-flex items-center justify-center", s.box)}>
        {/* 柔光晕：呼吸感 */}
        <span
          aria-hidden
          className={cn("absolute rounded-full blur-md animate-pulse", s.halo, t.halo)}
        />
        {/* 旋转环：top + right 双段不同透明度，形成方向感而非匀质圆环 */}
        <span
          aria-hidden
          className={cn(
            "absolute inset-0 rounded-full border-transparent animate-spin",
            s.ring,
            t.ring,
          )}
        />
        {/* 中心呼吸点 */}
        <span
          aria-hidden
          className={cn("absolute rounded-full animate-pulse", s.dot, t.dot)}
        />
      </div>
      {label && (
        <p className={cn("font-medium tracking-wide", s.text, t.text)}>{label}</p>
      )}
    </div>
  );

  if (variant === "card") {
    return (
      <div
        className={cn(
          "border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex items-center justify-center min-h-[400px]",
          className,
        )}
      >
        {inner}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex items-center justify-center",
        fullHeight && "h-full w-full",
        className,
      )}
    >
      {inner}
    </div>
  );
}

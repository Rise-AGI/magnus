// front_end/src/lib/utils.ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function formatBeijingTime(isoString: string | undefined | null) {
  if (!isoString) return "--";
  const date = new Date(isoString.endsWith("Z") ? isoString : `${isoString}Z`);
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).replace(/\//g, '-');
}

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
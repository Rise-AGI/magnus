// front_end/src/hooks/use-from-href.ts
"use client";

import { useCallback } from "react";
import { usePathname, useSearchParams } from "next/navigation";


/**
 * Source 侧 helper：把"当前完整 URL（path + query）"包成 ?from= 注入到 target。
 *
 * 配合 detail 页的 useBackNavigation 使用。关键点是带上 query string —— 这样
 * 从 /jobs?owner_id=alice 点进 detail，back 时能落回过滤后的列表，而不是裸 /jobs。
 *
 * 用法：
 *   const buildFromHref = useFromHref();
 *   router.push(buildFromHref(`/jobs/${id}`));
 *
 * target 自己可以已经带 query（如 chip 跳 `/jobs?owner_id=x`），sep 自动选 `?` 或 `&`。
 */
export function useFromHref() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  return useCallback((target: string): string => {
    const qs = searchParams.toString();
    const here = qs ? `${pathname}?${qs}` : pathname;
    const sep = target.includes("?") ? "&" : "?";
    return `${target}${sep}from=${encodeURIComponent(here)}`;
  }, [pathname, searchParams]);
}

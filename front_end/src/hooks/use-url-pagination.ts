// front_end/src/hooks/use-url-pagination.ts
"use client";

import { useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";


const DEFAULT_PAGE_SIZE = 10;


/**
 * Keeps a list page's pagination in the URL query string so it survives route
 * round-trips — the same ?from= / useBackNavigation machinery that already
 * restores filters like owner_id then restores the page for free.
 *
 * `prefix` namespaces the params so a page hosting several independent tables
 * (the cluster dashboard) can run multiple instances side by side
 * (`my_page`, `running_page`, …).
 *
 * `setParams` merges against `window.location.search` read at call time rather
 * than a render-time `useSearchParams()` snapshot. That keeps it a stable
 * callback (safe in effect deps) and, more importantly, means a sibling writer
 * firing in the same tick — an owner_id filter change, a focus-param cleanup —
 * can neither drop our pagination params nor have its own dropped. Page/size
 * are dropped from the URL at their defaults so an untouched list keeps a clean
 * address.
 */
export function useUrlPagination(
  options?: {
    prefix?: string;
    defaultPageSize?: number;
  },
) {
  const prefix = options?.prefix ? `${options.prefix}_` : "";
  const defaultPageSize = options?.defaultPageSize ?? DEFAULT_PAGE_SIZE;
  const pageKey = `${prefix}page`;
  const sizeKey = `${prefix}page_size`;

  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const pageRaw = Number(searchParams.get(pageKey));
  const page = Number.isInteger(pageRaw) && pageRaw > 1 ? pageRaw : 1;

  const sizeRaw = Number(searchParams.get(sizeKey));
  const pageSize = Number.isInteger(sizeRaw) && sizeRaw > 0 ? sizeRaw : defaultPageSize;

  const setParams = useCallback(
    (patch: Record<string, string | number | null>) => {
      const params = new URLSearchParams(window.location.search);
      for (const [key, value] of Object.entries(patch)) {
        if (value === null || value === "") params.delete(key);
        else params.set(key, String(value));
      }
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname],
  );

  const setPage = useCallback(
    (next: number) => {
      setParams({ [pageKey]: next > 1 ? next : null });
    },
    [setParams, pageKey],
  );

  const setPageSize = useCallback(
    (next: number) => {
      // Changing the page size resets to the first page in the same atomic write.
      setParams({ [sizeKey]: next === defaultPageSize ? null : next, [pageKey]: null });
    },
    [setParams, sizeKey, pageKey, defaultPageSize],
  );

  return {
    page,
    pageSize,
    setPage,
    setPageSize,
    setParams,
  };
}

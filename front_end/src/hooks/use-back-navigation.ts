// front_end/src/hooks/use-back-navigation.ts
"use client";

import { useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useLanguage } from "@/context/language-context";


/**
 * Reads the `from` query parameter to provide context-aware back navigation.
 *
 * Source side: append `?from=/current/path` when linking.
 * Target side: call this hook to get { backPath, backLabel, goBack }.
 *
 * When `from` is set, `goBack` uses `router.replace` to avoid history-stack traps
 * (push would create A→B→A→B… loops). When not set, falls back to `router.push`
 * with the provided defaults (preserving existing behavior).
 */
export function useBackNavigation(defaultPath: string, defaultLabel: string) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { t } = useLanguage();

  const fromParam = searchParams.get("from");

  let backPath = defaultPath;
  let backLabel = defaultLabel;
  let hasReferrer = false;

  if (fromParam) {
    try {
      const decoded = decodeURIComponent(fromParam);
      // Only allow internal paths; block protocol-relative "//evil.com" (open redirect)
      if (decoded.startsWith("/") && !decoded.startsWith("//")) {
        backPath = decoded;
        backLabel = t("common.back");
        hasReferrer = true;
      }
    } catch {
      // malformed URI — fall through to defaults
    }
  }

  const goBack = useCallback(() => {
    if (hasReferrer) {
      router.replace(backPath);
    } else {
      router.push(backPath);
    }
  }, [router, backPath, hasReferrer]);

  return { backPath, backLabel, goBack };
}

"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { DEFAULT_EXECUTION_TARGET } from "@/lib/config";
import { EXECUTION_TARGET_STORAGE_KEY } from "@/lib/execution-target-navigation";

const ExecutionTargetContext = createContext(DEFAULT_EXECUTION_TARGET);

export function ExecutionTargetProvider({ children }: { children: React.ReactNode }) {
  const searchParams = useSearchParams();
  const requested = searchParams.get("execution_target");
  const [remembered, setRemembered] = useState(DEFAULT_EXECUTION_TARGET);

  useEffect(() => {
    if (requested !== null) setRemembered(requested);
    try {
      if (requested !== null) {
        sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, requested);
      } else {
        const stored = sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY);
        if (stored) setRemembered(stored);
      }
    } catch {
      // A blocked browser store must not prevent in-tab target selection.
    }
  }, [requested]);

  return (
    <ExecutionTargetContext.Provider value={requested ?? remembered}>
      {children}
    </ExecutionTargetContext.Provider>
  );
}

export function useExecutionTarget(): string {
  return useContext(ExecutionTargetContext);
}

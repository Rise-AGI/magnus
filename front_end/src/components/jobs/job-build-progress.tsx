"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import * as Popover from "@radix-ui/react-popover";
import { Check, Loader2, X } from "lucide-react";
import { client } from "@/lib/api";
import { useLanguage } from "@/context/language-context";
import type { JobBuildProgress } from "@/types/job";

interface JobBuildProgressProps {
  jobId: string;
  status: string;
  onViewBuildLogs?: () => void;
  children: React.ReactNode;
}

export function JobBuildProgressPopover({ jobId, status, onViewBuildLogs, children }: JobBuildProgressProps) {
  const { t } = useLanguage();
  const contentId = useId();
  const [open, setOpen] = useState(false);
  const [progress, setProgress] = useState<JobBuildProgress | null>(null);
  const [error, setError] = useState(false);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const clearHoverTimer = () => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
  };

  useEffect(() => () => clearHoverTimer(), []);

  useEffect(() => {
    if (!open) return;
    let disposed = false;
    let terminal = false;
    let inFlight = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setProgress(null);
    setError(false);

    const fetchProgress = async () => {
      if (disposed || inFlight || document.hidden || terminal) return;
      inFlight = true;
      controller = new AbortController();
      const signal = controller.signal;
      try {
        const data: JobBuildProgress = await client(`/api/jobs/${jobId}/build`, { signal });
        if (disposed || signal.aborted) return;
        // A recovered Job may briefly expose the previous attempt's terminal
        // snapshot. Its Building status remains authoritative for polling.
        terminal = data.terminal && status !== "Building";
        setProgress(data);
        setError(false);
      } catch {
        if (!disposed && !signal.aborted) setError(true);
      } finally {
        inFlight = false;
        if (!disposed && !document.hidden && !terminal && status === "Building") {
          timer = setTimeout(fetchProgress, 2000);
        }
      }
    };

    const onVisibilityChange = () => {
      clearTimeout(timer);
      if (document.hidden) controller?.abort();
      else void fetchProgress();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    void fetchProgress();
    return () => {
      disposed = true;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [open, jobId, status]);

  const closeAfterHover = () => {
    clearHoverTimer();
    hoverTimer.current = setTimeout(() => {
      const focused = document.activeElement;
      if (focused !== triggerRef.current && !contentRef.current?.contains(focused)) setOpen(false);
    }, 180);
  };

  return (
    <Popover.Root open={open} onOpenChange={next => { clearHoverTimer(); setOpen(next); }}>
      <Popover.Anchor asChild>
        <button
          ref={triggerRef}
          type="button"
          aria-label={t("jobBuild.title")}
          aria-expanded={open}
          aria-haspopup="dialog"
          aria-controls={open ? contentId : undefined}
          className="inline-flex text-left rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          onPointerEnter={event => {
            if (event.pointerType !== "mouse") return;
            clearHoverTimer();
            hoverTimer.current = setTimeout(() => setOpen(true), 250);
          }}
          onPointerLeave={closeAfterHover}
          onFocus={event => { if (event.currentTarget.matches(":focus-visible")) setOpen(true); }}
          onBlur={closeAfterHover}
          onClick={event => { event.stopPropagation(); clearHoverTimer(); setOpen(true); }}
          onKeyDown={event => {
            if (event.key === "Escape") { clearHoverTimer(); setOpen(false); }
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setOpen(true);
              requestAnimationFrame(() => contentRef.current?.querySelector<HTMLButtonElement>("button")?.focus());
            }
          }}
        >
          {children}
        </button>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content
          ref={contentRef}
          id={contentId}
          sideOffset={8}
          collisionPadding={12}
          align="start"
          aria-label={t("jobBuild.title")}
          onOpenAutoFocus={event => event.preventDefault()}
          onCloseAutoFocus={event => event.preventDefault()}
          onPointerEnter={clearHoverTimer}
          onPointerLeave={closeAfterHover}
          onBlur={closeAfterHover}
          onClick={event => event.stopPropagation()}
          className="z-[210] w-[420px] max-w-[calc(100vw-24px)] rounded-xl border border-zinc-700 bg-zinc-950 p-4 shadow-2xl text-left text-sm whitespace-normal"
        >
          <div className="flex items-center justify-between gap-4 mb-3">
            <span className="font-semibold text-zinc-200">{t("jobBuild.title")}</span>
            <Popover.Close aria-label={t("common.close")} className="text-zinc-500 hover:text-zinc-200"><X className="w-4 h-4" /></Popover.Close>
          </div>
          {error ? <p role="status" className="text-red-400 text-xs">{t("jobBuild.loadFailed")}</p> : !progress ? (
            <Loader2 aria-label={t("jobBuild.loading")} className="w-4 h-4 animate-spin text-zinc-400" />
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="text-cyan-400">{t(`jobBuild.phase.${progress.phase}`)}</span>
                <span className="text-zinc-500 tabular-nums">{t("jobBuild.elapsed", { seconds: Math.floor(progress.elapsed_seconds).toString() })}</span>
              </div>
              <p className="text-xs text-zinc-500">{t("jobBuild.steps", { completed: String(progress.completed_steps), total: String(progress.total_steps), cached: String(progress.cached_steps) })}</p>
              {progress.current_step && <p className="text-xs text-zinc-300 break-words">{progress.current_step}</p>}
              {progress.steps.length > 0 && (
                <ul className="max-h-36 overflow-auto space-y-1 text-xs">
                  {progress.steps.map(step => (
                    <li key={step.id} className={`flex items-start gap-2 ${step.error ? "text-red-400" : "text-zinc-400"}`}>
                      {step.completed || step.cached ? <Check className="h-3 w-3 mt-0.5 shrink-0" /> : <span className="h-3 w-3 shrink-0" />}
                      <span className="break-words min-w-0">{step.name}{step.cached ? ` · ${t("jobBuild.cached")}` : ""}{step.error ? ` · ${step.error}` : ""}</span>
                    </li>
                  ))}
                </ul>
              )}
              {progress.error && <p className="text-xs text-red-400 break-words">{progress.error}</p>}
              {progress.log_tail && <pre className="max-h-40 overflow-auto rounded bg-black/40 p-2 text-[11px] text-zinc-400 whitespace-pre-wrap break-all">{progress.log_tail}</pre>}
            </div>
          )}
          <Link
            href={`/jobs/${encodeURIComponent(jobId)}?logs=build`}
            className="inline-block mt-3 text-xs text-blue-400 hover:text-blue-300"
            onClick={event => {
              setOpen(false);
              if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) onViewBuildLogs?.();
            }}
          >
            {t("jobBuild.viewLogs")}
          </Link>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

// front_end/src/components/services/service-table.tsx
"use client";

import { useRouter } from "next/navigation";
import { Loader2, Power, RefreshCw, Trash2, Box } from "lucide-react";
import { Service } from "@/types/service";
import { CopyableText } from "@/components/ui/copyable-text";
import { TransferableAuthor } from "@/components/ui/transferable-author";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { formatBeijingTime } from "@/lib/utils";
import { useLanguage } from "@/context/language-context";
import { useIsMobile } from "@/hooks/use-is-mobile";

interface ServiceTableProps {
  services: Service[];
  loading: boolean;
  onClone: (service: Service) => void;
  onToggle: (service: Service) => void;
  onDelete: (service: Service) => void;
  onRefresh?: () => void;
  emptyMessage?: string;
  className?: string;
}

export function ServiceTable({
  services,
  loading,
  onClone,
  onToggle,
  onDelete,
  onRefresh,
  emptyMessage,
  className = "",
}: ServiceTableProps) {
  const { t } = useLanguage();
  const router = useRouter();
  const isMobile = useIsMobile();

  if (loading) {
    return (
      <div className={`border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 gap-3 min-h-[400px] ${className}`}>
        <Loader2 className="w-8 h-8 animate-spin text-teal-500" />
        <p className="text-sm font-medium">{t("services.fetching")}</p>
      </div>
    );
  }

  if (services.length === 0) {
    return (
      <div className={`border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 min-h-[400px] ${className}`}>
        <Box className="w-10 h-10 opacity-20 mb-3" />
        <p className="text-base font-medium text-zinc-400">{emptyMessage || t("services.noFound")}</p>
      </div>
    );
  }

  if (isMobile) {
    return (
      <div className={`space-y-3 ${className}`}>
        {services.map((svc) => {
          const isJobAlive = svc.current_job?.status && ["Pending", "Preparing", "Running", "Paused"].includes(svc.current_job.status);

          return (
            <div
              key={svc.id}
              onClick={() => router.push(`/services/${svc.id}`)}
              className="border border-zinc-800 rounded-xl bg-zinc-900/40 p-4 active:bg-zinc-800/60 transition-colors"
            >
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-zinc-200 text-sm truncate">{svc.name}</p>
                  <CopyableText text={svc.id} className="text-[10px] tracking-wider" />
                </div>
                <div className="flex-shrink-0">
                  {!svc.is_active ? (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-zinc-800 text-zinc-500 border border-zinc-700">{t("services.inactive")}</span>
                  ) : isJobAlive ? (
                    <div className="scale-90"><JobStatusBadge status={svc.current_job!.status} /></div>
                  ) : (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-teal-500/10 text-teal-400 border border-teal-500/20">{t("services.idle")}</span>
                  )}
                </div>
              </div>
              {svc.description && (
                <p className="text-zinc-400 text-xs leading-relaxed line-clamp-2 mb-3">{svc.description}</p>
              )}
              <div className="flex items-center justify-between">
                <span className="text-xs text-zinc-500">{formatBeijingTime(svc.updated_at)}</span>
                <div className="flex gap-2">
                  <button onClick={(e) => { e.stopPropagation(); onClone(svc); }} className="p-3 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-zinc-400 border border-zinc-700/50 active:scale-95">
                    <RefreshCw className="w-4 h-4" />
                  </button>
                  {svc.can_manage && (
                    <button onClick={(e) => { e.stopPropagation(); onToggle(svc); }} className={`p-3 rounded-lg border active:scale-95 ${svc.is_active ? "bg-teal-900/20 text-teal-400 border-teal-500/20" : "bg-zinc-800 text-zinc-500 border-zinc-700/50"}`}>
                      <Power className="w-4 h-4" />
                    </button>
                  )}
                  {svc.can_manage && (
                    <button onClick={(e) => { e.stopPropagation(); onDelete(svc); }} className="p-3 bg-red-950/30 hover:bg-red-900/50 text-red-400 rounded-lg border border-red-900/30 active:scale-95">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className={`border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col overflow-hidden min-h-[400px] ${className}`}>
      <div className="overflow-x-auto w-full">
        <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
          <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
            <tr>
              <th className="px-6 py-4 font-medium w-[22%]">{t("services.table.service")}</th>
              <th className="px-6 py-4 font-medium w-[36%]">{t("services.table.description")}</th>
              <th className="px-6 py-4 font-medium w-[12%] text-center">{t("services.table.jobStatus")}</th>
              <th className="px-6 py-4 font-medium w-[15%]">{t("services.table.manager")}</th>
              <th className="px-6 py-4 font-medium text-right w-[15%]"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {services.map((svc) => {
              const displayUser = svc.owner || {
                id: svc.owner_id,
                name: "Unknown",
                feishu_open_id: "",
                email: "",
                avatar_url: null
              };

              let statusNode;

              if (!svc.is_active) {
                // Manual Inactive State
                statusNode = (
                  <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-zinc-800 text-zinc-500 border border-zinc-700">
                    {t("services.inactive")}
                  </span>
                );
              } else {
                // Active State Check
                const currentStatus = svc.current_job?.status;
                const isJobAlive = currentStatus && ["Pending", "Preparing", "Running", "Paused"].includes(currentStatus);

                if (isJobAlive) {
                  // Active Job Running
                  statusNode = (
                    <div className="scale-90">
                      <JobStatusBadge status={currentStatus} />
                    </div>
                  );
                } else {
                  // Idle State (Active service but no live job / Scale-to-Zero)
                  statusNode = (
                    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-teal-500/10 text-teal-400 border border-teal-500/20">
                      <span className="relative flex h-2 w-2 mr-1.5">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-teal-400 opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-2 w-2 bg-teal-500"></span>
                      </span>
                      {t("services.idle")}
                    </span>
                  );
                }
              }

              if (svc.current_job) {
                const originalNode = statusNode;
                statusNode = (
                  <div
                    onClick={(e) => {
                      e.stopPropagation();
                      router.push(`/jobs/${svc.current_job!.id}?from=/services`);
                    }}
                    className="cursor-pointer hover:opacity-80 transition-opacity block"
                  >
                    {originalNode}
                  </div>
                );
              }

              return (
                <tr
                  key={svc.id}
                  onClick={() => {
                    const sel = window.getSelection();
                    if (sel && sel.toString().length > 0) return;
                    router.push(`/services/${svc.id}`);
                  }}
                  className="hover:bg-zinc-800/40 transition-colors group border-b border-zinc-800/50 last:border-0"
                >
                  {/* Column 1: Service / ID */}
                  <td className="px-6 py-4 align-top whitespace-normal break-all">
                    <div className="flex flex-col gap-1.5">
                      <div className="flex items-center gap-2">
                        <CopyableText
                          text={svc.name}
                          variant="text"
                          className="font-semibold text-zinc-200 text-base"
                        />
                      </div>
                      <div className="flex items-center gap-2">
                        <CopyableText text={svc.id} className="text-[10px] tracking-wider" />
                      </div>
                    </div>
                  </td>

                  {/* Column 2: Description */}
                  <td className="px-6 py-4 align-top whitespace-normal">
                    <p className="text-zinc-400 text-sm leading-relaxed break-all">
                      {svc.description || <span className="text-zinc-600 italic">{t("services.noDescription")}</span>}
                    </p>
                  </td>

                  {/* Column 3: Job Status */}
                  <td className="px-6 py-4 align-top">
                    <div className="flex justify-center h-full pt-1">
                      {statusNode}
                    </div>
                  </td>

                  {/* Column 4: Creator / Updated at */}
                  <td className="px-6 py-4 align-top">
                    <div>
                      <TransferableAuthor
                        user={{
                          ...displayUser,
                          email: displayUser.email || undefined,
                          avatar_url: displayUser.avatar_url || undefined
                        }}
                        canTransfer={!!svc.can_manage}
                        entityType="services"
                        entityId={svc.id}
                        entityTitle={svc.name}
                        avatarSize="sm"
                        subText={formatBeijingTime(svc.updated_at)}
                        onTransferred={() => onRefresh?.()}
                      />
                    </div>
                  </td>

                  {/* Column 5: Actions */}
                  <td className="px-6 py-4 align-middle text-right">
                    <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all transform translate-x-2 group-hover:translate-x-0">

                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onClone(svc);
                        }}
                        className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                        title={svc.can_manage ? t("services.editService") : t("services.cloneService")}
                      >
                        <RefreshCw className="w-4 h-4" />
                      </button>

                      {svc.can_manage && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onToggle(svc);
                          }}
                          className={`p-2 rounded-lg transition-colors border shadow-sm ${svc.is_active
                              ? "bg-teal-900/20 hover:bg-teal-900/40 text-teal-400 border-teal-500/20"
                              : "bg-zinc-800 hover:bg-zinc-700 text-zinc-500 hover:text-zinc-300 border-zinc-700/50"
                            }`}
                          title={svc.is_active ? "Stop Service" : "Start Service"}
                        >
                          <Power className="w-4 h-4" />
                        </button>
                      )}

                      {svc.can_manage && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onDelete(svc);
                          }}
                          className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                          title={t("common.delete")}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}

                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
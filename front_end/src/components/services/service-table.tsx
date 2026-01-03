// front_end/src/components/services/service-table.tsx
"use client";

import { Loader2, Power, RefreshCw, Trash2, Box } from "lucide-react";
import { Service } from "@/types/service";
import { CopyableText } from "@/components/ui/copyable-text";
import { UserAvatar } from "@/components/ui/user-avatar";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { formatBeijingTime } from "@/lib/utils";
import { useAuth } from "@/context/auth-context";

interface ServiceTableProps {
  services: Service[];
  loading: boolean;
  onClone: (service: Service) => void;
  onToggle: (service: Service) => void;
  onDelete: (service: Service) => void;
  emptyMessage?: string;
  className?: string;
}

export function ServiceTable({
  services,
  loading,
  onClone,
  onToggle,
  onDelete,
  emptyMessage = "No services found.",
  className = "",
}: ServiceTableProps) {
  const { user: currentUser } = useAuth();

  if (loading) {
    return (
      <div className={`border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 gap-3 min-h-[400px] ${className}`}>
        <Loader2 className="w-8 h-8 animate-spin text-teal-500" />
        <p className="text-sm font-medium">Fetching services...</p>
      </div>
    );
  }

  if (services.length === 0) {
    return (
      <div className={`border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 min-h-[400px] ${className}`}>
        <Box className="w-10 h-10 opacity-20 mb-3" />
        <p className="text-base font-medium text-zinc-400">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className={`border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col overflow-hidden min-h-[400px] ${className}`}>
      <div className="overflow-x-auto w-full">
        <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
          <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
            <tr>
              <th className="px-6 py-4 font-medium w-[25%]">Service / Service ID</th>
              <th className="px-6 py-4 font-medium w-[45%]">Description</th>
              <th className="px-6 py-4 font-medium w-[15%] text-center">Job Status</th>
              <th className="px-6 py-4 font-medium w-[15%] text-center">Creator / Updated at</th>
              <th className="px-6 py-4 font-medium text-right w-[15%]"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {services.map((svc) => {
              const isOwner = currentUser?.id === svc.owner_id;

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
                    Inactive
                  </span>
                );
              } else {
                // Active State Check
                const currentStatus = svc.current_job?.status;
                const isJobAlive = currentStatus && ["Pending", "Running", "Paused"].includes(currentStatus);

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
                      Idle
                    </span>
                  );
                }
              }

              return (
                <tr
                  key={svc.id}
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
                    <p className="text-zinc-400 text-sm leading-relaxed line-clamp-2 break-words">
                      {svc.description || <span className="text-zinc-600 italic">No description provided.</span>}
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
                    <div className="flex justify-center">
                      <UserAvatar
                        user={{
                          ...displayUser,
                          email: displayUser.email || undefined,
                          avatar_url: displayUser.avatar_url || undefined
                        }}
                        subText={formatBeijingTime(svc.updated_at)}
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
                        title={isOwner ? "Edit Service" : "Clone Service"}
                      >
                        <RefreshCw className="w-4 h-4" />
                      </button>

                      {isOwner && (
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

                      {isOwner && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onDelete(svc);
                          }}
                          className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                          title="Delete"
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
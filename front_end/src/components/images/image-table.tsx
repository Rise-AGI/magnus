// front_end/src/components/images/image-table.tsx
"use client";

import { RefreshCw, Trash2, Container, Loader2 } from "lucide-react";
import { formatBeijingTime } from "@/lib/utils";
import { UserAvatar } from "@/components/ui/user-avatar";
import { CopyableText } from "@/components/ui/copyable-text";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { User } from "@/types/auth";

export interface CachedImage {
  id: number | null;
  uri: string;
  filename: string;
  user_id: string | null;
  user: { id: string; name: string; avatar_url?: string; email?: string } | null;
  status: string;
  size_bytes: number;
  created_at: string | null;
  updated_at: string | null;
}

export function formatSize(bytes: number): string {
  if (bytes <= 0) return "-";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export const STATUS_STYLES: Record<string, string> = {
  cached: "bg-green-900/30 text-green-400 border-green-800/50",
  refreshing: "bg-yellow-900/30 text-yellow-400 border-yellow-800/50",
  pulling: "bg-blue-900/30 text-blue-400 border-blue-800/50",
  unregistered: "bg-zinc-800/50 text-zinc-500 border-zinc-700/50",
  missing: "bg-red-900/30 text-red-400 border-red-800/50",
};

export const STATUS_I18N: Record<string, string> = {
  cached: "images.status.cached",
  refreshing: "images.status.refreshing",
  pulling: "images.status.pulling",
  unregistered: "images.status.unregistered",
  missing: "images.status.missing",
};

export const isBusy = (s: string) => s === "refreshing" || s === "pulling";

interface ImageTableProps {
  data: CachedImage[];
  loading: boolean;
  onView: (image: CachedImage) => void;
  onDelete: (image: CachedImage) => void;
}

export function ImageTable({ data, loading, onView, onDelete }: ImageTableProps) {
  const { user: currentUser } = useAuth();
  const { t } = useLanguage();

  if (loading) {
    return (
      <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 gap-3 min-h-[400px]">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
        <p className="text-sm font-medium">{t("images.fetching")}</p>
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 min-h-[400px]">
        <Container className="w-10 h-10 opacity-20 mb-3" />
        <p className="text-base font-medium text-zinc-400">{t("images.noFound")}</p>
      </div>
    );
  }

  return (
    <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col overflow-hidden min-h-[400px]">
      <div className="overflow-x-auto w-full">
        <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
          <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
            <tr>
              <th className="px-6 py-4 font-medium w-[50%]">URI</th>
              <th className="px-6 py-4 font-medium w-[15%]">{t("images.table.owner")}</th>
              <th className="px-6 py-4 font-medium w-[10%] text-right">{t("images.table.size")}</th>
              <th className="px-6 py-4 font-medium w-[12%] text-center">{t("images.table.status")}</th>
              <th className="px-6 py-4 font-medium text-right w-[13%]"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {data.map((img, idx) => {
              const isOwner = currentUser?.id === img.user_id;
              const canManage = isOwner || currentUser?.is_admin;
              const busy = isBusy(img.status);

              const displayUser = img.user ? {
                id: img.user.id,
                name: img.user.name,
                feishu_open_id: "",
                email: img.user.email || undefined,
                avatar_url: img.user.avatar_url || undefined,
              } as User : undefined;

              const statusKey = STATUS_I18N[img.status] as any;
              const statusLabel = statusKey ? t(statusKey) : img.status;

              return (
                <tr key={img.id ?? `fs-${idx}`} className="hover:bg-zinc-800/40 transition-colors group border-b border-zinc-800/50 last:border-0">
                  <td className="px-6 py-4 align-top whitespace-normal break-all">
                    <CopyableText text={img.uri} variant="text" className="font-semibold text-zinc-200 text-base" />
                  </td>
                  <td className="px-6 py-4 align-top">
                    <div>
                      <UserAvatar
                        user={displayUser}
                        subText={img.updated_at ? formatBeijingTime(img.updated_at) : ""}
                      />
                    </div>
                  </td>
                  <td className="px-6 py-4 text-right text-zinc-400 font-mono text-xs">
                    {formatSize(img.size_bytes)}
                  </td>
                  <td className="px-6 py-4 text-center">
                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${STATUS_STYLES[img.status] || "bg-zinc-800 text-zinc-400 border-zinc-700"}`}>
                      {busy && <Loader2 className="w-3 h-3 animate-spin" />}
                      {statusLabel}
                    </span>
                  </td>
                  <td className="px-6 py-4 align-middle text-right">
                    <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all transform translate-x-2 group-hover:translate-x-0">
                      {canManage && img.id !== null && (
                        <>
                          <button
                            onClick={() => onView(img)}
                            className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                            title={t("images.refresh")}
                          >
                            <RefreshCw className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} />
                          </button>
                          <button
                            onClick={() => onDelete(img)}
                            disabled={busy}
                            className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30 disabled:opacity-30 disabled:cursor-not-allowed"
                            title={t("common.delete")}
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </>
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

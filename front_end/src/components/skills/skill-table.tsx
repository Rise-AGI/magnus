// front_end/src/components/skills/skill-table.tsx
"use client";

import { useRouter } from "next/navigation";
import { RefreshCw, Trash2, Dna, Loader2 } from "lucide-react";
import { formatBeijingTime } from "@/lib/utils";
import { CopyableText } from "@/components/ui/copyable-text";
import { TransferableAuthor } from "@/components/ui/transferable-author";
import { useLanguage } from "@/context/language-context";
import { Skill } from "@/types/skill";
import { useIsMobile } from "@/hooks/use-is-mobile";

interface SkillTableProps {
  data: Skill[];
  loading: boolean;
  onClone: (skill: Skill) => void;
  onDelete: (skill: Skill) => void;
  onRefresh?: () => void;
  emptyMessage?: string;
}

export function SkillTable({
  data,
  loading,
  onClone,
  onDelete,
  onRefresh,
  emptyMessage,
}: SkillTableProps) {

  const router = useRouter();
  const { t } = useLanguage();
  const isMobile = useIsMobile();

  if (loading) {
    return (
      <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 gap-3 min-h-[400px]">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
        <p className="text-sm font-medium">{t("skills.fetching")}</p>
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 min-h-[400px]">
        <Dna className="w-10 h-10 opacity-20 mb-3" />
        <p className="text-base font-medium text-zinc-400">{emptyMessage || t("skills.noFound")}</p>
      </div>
    );
  }

  if (isMobile) {
    return (
      <div className="space-y-3">
        {data.map((skill) => (
          <div
            key={skill.id}
            onClick={() => router.push(`/skills/${skill.id}`)}
            className="border border-zinc-800 rounded-xl bg-zinc-900/40 p-4 active:bg-zinc-800/60 transition-colors"
          >
            <div className="flex items-start justify-between gap-2 mb-2">
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-zinc-200 text-sm truncate">{skill.title}</p>
                <CopyableText text={skill.id} className="text-[10px] tracking-wider" />
              </div>
            </div>
            {skill.description && (
              <p className="text-zinc-400 text-xs leading-relaxed line-clamp-2 mb-3">{skill.description}</p>
            )}
            <div className="flex items-center justify-between">
              <span className="text-xs text-zinc-500">{formatBeijingTime(skill.updated_at)}</span>
              <div className="flex gap-2">
                <button onClick={(e) => { e.stopPropagation(); onClone(skill); }} className="p-3 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-zinc-400 border border-zinc-700/50 active:scale-95" title={t("skills.clone")}>
                  <RefreshCw className="w-4 h-4" />
                </button>
                {skill.can_manage && (
                  <button onClick={(e) => { e.stopPropagation(); onDelete(skill); }} className="p-3 bg-red-950/30 hover:bg-red-900/50 text-red-400 rounded-lg border border-red-900/30 active:scale-95" title={t("common.delete")}>
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col overflow-hidden min-h-[400px]">
      <div className="overflow-x-auto w-full">
        <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
          <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
            <tr>
              <th className="px-6 py-4 font-medium w-[25%]">{t("skills.table.skill")}</th>
              <th className="px-6 py-4 font-medium w-[45%]">{t("skills.table.description")}</th>
              <th className="px-6 py-4 font-medium w-[15%]">{t("skills.table.author")}</th>
              <th className="px-6 py-4 font-medium text-right w-[15%]"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {data.map((skill) => {
              const displayUser = skill.user || {
                id: skill.user_id,
                name: "Unknown",
                feishu_open_id: "",
                email: ""
              };

              return (
                <tr
                  key={skill.id}
                  onClick={() => {
                    const sel = window.getSelection();
                    if (sel && sel.toString().length > 0) return;
                    router.push(`/skills/${skill.id}`);
                  }}
                  className="hover:bg-zinc-800/40 transition-colors group border-b border-zinc-800/50 last:border-0"
                >
                  <td className="px-6 py-4 align-top whitespace-normal break-all">
                    <div className="flex flex-col gap-1.5">
                      <div className="flex items-center gap-2">
                        <CopyableText text={skill.title} variant="text" className="font-semibold text-zinc-200 text-base" />
                      </div>
                      <div className="flex items-center gap-2">
                        <CopyableText text={skill.id} className="text-[10px] tracking-wider" />
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 align-top whitespace-normal">
                    {skill.description ? (
                      <p className="text-zinc-400 text-sm leading-relaxed break-words whitespace-pre-line">{skill.description}</p>
                    ) : (
                      <p className="text-zinc-600 text-sm italic">{t("skills.table.noDescription")}</p>
                    )}
                  </td>
                  <td className="px-6 py-4 align-top">
                    <div>
                      <TransferableAuthor
                        user={{
                            ...displayUser,
                            email: displayUser.email || undefined,
                            avatar_url: displayUser.avatar_url || undefined
                        }}
                        canTransfer={!!skill.can_manage}
                        entityType="skills"
                        entityId={skill.id}
                        entityTitle={skill.title}
                        avatarSize="sm"
                        subText={formatBeijingTime(skill.updated_at)}
                        onTransferred={() => onRefresh?.()}
                      />
                    </div>
                  </td>
                  <td className="px-6 py-4 align-middle text-right">
                    <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all transform translate-x-2 group-hover:translate-x-0">
                      <button onClick={(e) => { e.stopPropagation(); onClone(skill); }} className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm" title={t("skills.clone")}>
                        <RefreshCw className="w-4 h-4" />
                      </button>
                      {skill.can_manage && (
                        <button onClick={(e) => { e.stopPropagation(); onDelete(skill); }} className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30" title={t("common.delete")}>
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

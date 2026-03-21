// front_end/src/components/people/people-table.tsx
"use client";

import { useState } from "react";
import { MessageCircle, RefreshCw, Trash2, Users, Loader2, Shield, UserX, UserPlus } from "lucide-react";
import { useLanguage } from "@/context/language-context";
import { useAuth } from "@/context/auth-context";
import { UserDetail } from "@/types/auth";


interface PeopleTableProps {
  data: UserDetail[];
  loading: boolean;
  onManage: (user: UserDetail) => void;
  onDelete: (user: UserDetail) => void;
  onChat?: (user: UserDetail) => void;
  onInviteToGroup?: (user: UserDetail) => void;
}


function Avatar({ user, size = "sm" }: { user: { name: string; avatar_url?: string | null; user_type: string }; size?: "xs" | "sm" | "lg" }) {
  const [broken, setBroken] = useState(false);
  const dim = size === "lg" ? "w-16 h-16" : size === "xs" ? "w-7 h-7" : "w-9 h-9";
  const textSize = size === "lg" ? "text-xl" : size === "xs" ? "text-[10px]" : "text-xs";
  const iconSize = size === "lg" ? "w-7 h-7" : size === "xs" ? "w-3 h-3" : "w-4 h-4";

  return (
    <div className={`${dim} rounded-full bg-zinc-800 border border-zinc-700/50 flex-shrink-0 overflow-hidden flex items-center justify-center`}>
      {user.avatar_url && !broken ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={user.avatar_url} alt={user.name} className="w-full h-full object-cover" onError={() => setBroken(true)} />
      ) : broken ? (
        <UserX className={`${iconSize} text-zinc-600`} />
      ) : (
        <span className={`${textSize} font-bold text-zinc-400`}>{user.name.substring(0, 1).toUpperCase()}</span>
      )}
    </div>
  );
}

export { Avatar };


export function PeopleTable({ data, loading, onManage, onDelete, onChat, onInviteToGroup }: PeopleTableProps) {
  const { t } = useLanguage();
  const { user: currentUser } = useAuth();

  const canManage = (row: UserDetail) =>
    currentUser?.is_admin || row.id === currentUser?.id || row.parent_id === currentUser?.id;

  const headcountDisplay = (u: UserDetail) => {
    if (u.headcount == null) return "\u221E";
    const avail = u.available_headcount ?? u.headcount;
    return `${avail} / ${u.headcount}`;
  };

  if (loading) {
    return (
      <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 gap-3 min-h-[400px]">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
        <p className="text-sm font-medium">{t("people.fetching")}</p>
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 min-h-[400px]">
        <Users className="w-10 h-10 opacity-20 mb-3" />
        <p className="text-base font-medium text-zinc-400">{t("people.noFound")}</p>
      </div>
    );
  }

  return (
    <>
      <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col overflow-hidden min-h-[400px]">
        <div className="overflow-x-auto w-full">
          <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
            <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
              <tr>
                <th className="px-6 py-4 font-medium w-[25%]">{t("people.table.member")}</th>
                <th className="px-6 py-4 font-medium w-[25%]">{t("people.table.leader")}</th>
                <th className="px-6 py-4 font-medium w-[15%] text-center">{t("people.table.bpSvc")}</th>
                <th className="px-6 py-4 font-medium w-[15%] text-center">{t("people.table.headcount")}</th>
                <th className="px-6 py-4 font-medium text-right w-[20%]"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/50">
              {data.map((user) => (
                <tr
                  key={user.id}
                  className="hover:bg-zinc-800/40 transition-colors group border-b border-zinc-800/50 last:border-0"
                >
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <Avatar user={user} />
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-zinc-200">{user.name}</span>
                        {user.is_admin && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-900/30 text-amber-400 border border-amber-800/50">
                            <Shield className="w-2.5 h-2.5" />
                            {t("people.role.admin")}
                          </span>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-zinc-400 text-sm">
                    {user.parent_name ? (
                      <div className="flex items-center gap-1.5">
                        <Avatar user={{ name: user.parent_name, avatar_url: user.parent_avatar_url ?? null, user_type: "human" }} size="xs" />
                        <span>{user.parent_name}</span>
                      </div>
                    ) : (
                      <span className="text-zinc-600 italic">{t("people.leader.void")}</span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-center text-zinc-400 text-sm">
                    {user.blueprint_count} / {user.service_count}
                  </td>
                  <td className="px-6 py-4 text-center text-zinc-400 text-sm">
                    {headcountDisplay(user)}
                  </td>
                  <td className="px-6 py-4 align-middle text-right">
                    <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 transition-all transform translate-x-2 group-hover:translate-x-0">
                      {canManage(user) && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onManage(user); }}
                          className="p-2 bg-zinc-800 hover:bg-zinc-700 hover:text-white rounded-lg text-zinc-400 transition-colors border border-zinc-700/50 shadow-sm"
                          title={t("people.drawer.title")}
                        >
                          <RefreshCw className="w-4 h-4" />
                        </button>
                      )}
                      {user.id !== currentUser?.id && (
                        <>
                          <button
                            onClick={(e) => { e.stopPropagation(); onChat?.(user); }}
                            className="p-2 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 hover:text-blue-300 rounded-lg transition-colors border border-blue-500/30 cursor-pointer"
                            title={t("chat.directMessage")}
                          >
                            <MessageCircle className="w-4 h-4" />
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); onInviteToGroup?.(user); }}
                            className="p-2 bg-violet-600/20 hover:bg-violet-600/40 text-violet-400 hover:text-violet-300 rounded-lg transition-colors border border-violet-500/30 cursor-pointer"
                            title={t("chat.inviteToGroup")}
                          >
                            <UserPlus className="w-4 h-4" />
                          </button>
                        </>
                      )}
                      {user.user_type === "agent" && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onDelete(user); }}
                          className="p-2 bg-red-950/30 hover:bg-red-900/50 text-red-400 hover:text-red-300 rounded-lg transition-colors border border-red-900/30"
                          title={t("people.drawer.delete")}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

    </>
  );
}

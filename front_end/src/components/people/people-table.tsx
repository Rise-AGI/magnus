// front_end/src/components/people/people-table.tsx
"use client";

import { MessageCircle, RefreshCw, UserMinus, Users, Loader2, Shield, UserPlus } from "lucide-react";
import { AvatarCircle } from "@/components/ui/user-avatar";
import { useLanguage } from "@/context/language-context";
import { useAuth } from "@/context/auth-context";
import { UserDetail } from "@/types/auth";
import { useIsMobile } from "@/hooks/use-is-mobile";


interface PeopleTableProps {
  data: UserDetail[];
  loading: boolean;
  onManage: (user: UserDetail) => void;
  onDelete: (user: UserDetail) => void;
  onChat?: (user: UserDetail) => void;
  onInviteToGroup?: (user: UserDetail) => void;
}


export function PeopleTable({ data, loading, onManage, onDelete, onChat, onInviteToGroup }: PeopleTableProps) {
  const { t } = useLanguage();
  const { user: currentUser } = useAuth();
  const isMobile = useIsMobile();

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

  if (isMobile) {
    return (
      <>
        <div className="space-y-3">
          {data.map((user) => (
            <div
              key={user.id}
              className="border border-zinc-800 rounded-xl bg-zinc-900/40 p-4"
            >
              <div className="flex items-center gap-3 mb-3">
                <AvatarCircle user={user} size="sm" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-zinc-200 text-sm truncate">{user.name}</span>
                    {user.is_admin && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-900/30 text-amber-400 border border-amber-800/50">
                        <Shield className="w-2.5 h-2.5" />
                        {t("people.role.admin")}
                      </span>
                    )}
                  </div>
                  {user.parent_name && (
                    <span className="text-xs text-zinc-500">{t("people.table.leader")}: {user.parent_name}</span>
                  )}
                </div>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex gap-3 text-xs text-zinc-500">
                  <span>{t("people.table.bpSvc")}: {user.blueprint_count}/{user.service_count}/{user.skill_count}</span>
                  <span>{t("people.table.headcount")}: {headcountDisplay(user)}</span>
                </div>
                <div className="flex gap-2">
                  {canManage(user) && (
                    <button
                      onClick={() => onManage(user)}
                      className="p-3 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-zinc-400 border border-zinc-700/50 active:scale-95"
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                  )}
                  <button
                    onClick={() => onChat?.(user)}
                    className="p-3 bg-blue-600/20 text-blue-400 rounded-lg border border-blue-500/30 active:scale-95"
                  >
                    <MessageCircle className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => onInviteToGroup?.(user)}
                    className="p-3 bg-violet-600/20 text-violet-400 rounded-lg border border-violet-500/30 active:scale-95"
                  >
                    <UserPlus className="w-4 h-4" />
                  </button>
                  {user.user_type === "agent" && (
                    <button
                      onClick={() => onDelete(user)}
                      className="p-3 bg-red-950/30 text-red-400 rounded-lg border border-red-900/30 active:scale-95"
                    >
                      <UserMinus className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </>
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
                      <AvatarCircle user={user} size="sm" />
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
                        <AvatarCircle user={{ name: user.parent_name, avatar_url: user.parent_avatar_url ?? null }} size="xs" />
                        <span>{user.parent_name}</span>
                      </div>
                    ) : (
                      <span className="text-zinc-600 italic">{t("people.leader.void")}</span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-center text-zinc-400 text-sm">
                    {user.blueprint_count} / {user.service_count} / {user.skill_count}
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
                          title={t("people.drawer.offboard")}
                        >
                          <UserMinus className="w-4 h-4" />
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

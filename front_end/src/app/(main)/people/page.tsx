// front_end/src/app/(main)/people/page.tsx
"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Search, Plus, Users, Loader2, Shield } from "lucide-react";
import { client } from "@/lib/api";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { useLanguage } from "@/context/language-context";
import { useDebounce } from "@/hooks/use-debounce";
import { User } from "@/types/auth";

export default function PeoplePage() {
  const { t } = useLanguage();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebounce(searchQuery);
  const [showRecruitWip, setShowRecruitWip] = useState(false);

  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await client("/api/users");
      setUsers(res);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const filtered = useMemo(() => {
    const q = debouncedQuery.trim().toLowerCase();
    if (!q) return users;
    return users.filter(u =>
      u.name.toLowerCase().includes(q) ||
      (u.email || "").toLowerCase().includes(q)
    );
  }, [users, debouncedQuery]);

  useEffect(() => { setCurrentPage(1); }, [debouncedQuery]);

  const totalPages = Math.ceil(filtered.length / pageSize);
  const paginated = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  return (
    <div className="relative min-h-[calc(100vh-8rem)] pb-20">
      <style jsx global>{`
        ::-webkit-scrollbar { display: none; }
        html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">{t("nav.people")}</h1>
          <p className="text-zinc-500 text-sm mt-1">{t("people.subtitle")}</p>
        </div>
        <button
          onClick={() => setShowRecruitWip(true)}
          className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50"
        >
          <Plus className="w-4 h-4" /> {t("people.recruit")}
        </button>
      </div>

      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-1.5 mb-6 flex flex-wrap items-center gap-2 backdrop-blur-sm relative z-20">
        <div className="relative flex-1 group">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 group-focus-within:text-blue-500 transition-colors" />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t("people.searchPlaceholder")}
            className="w-full bg-transparent border-none py-2.5 pl-9 pr-4 text-sm text-zinc-200 focus:outline-none focus:ring-0 placeholder-zinc-600"
          />
        </div>
      </div>

      {loading ? (
        <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 gap-3 min-h-[400px]">
          <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
          <p className="text-sm font-medium">{t("people.fetching")}</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col items-center justify-center text-zinc-500 min-h-[400px]">
          <Users className="w-10 h-10 opacity-20 mb-3" />
          <p className="text-base font-medium text-zinc-400">{t("people.noFound")}</p>
        </div>
      ) : (
        <div className="border border-zinc-800 rounded-xl bg-zinc-900/40 backdrop-blur-sm shadow-sm flex flex-col overflow-hidden min-h-[400px]">
          <div className="overflow-x-auto w-full">
            <table className="w-full text-left text-sm whitespace-nowrap table-fixed">
              <thead className="bg-zinc-900/90 text-zinc-500 border-b border-zinc-800 backdrop-blur-md">
                <tr>
                  <th className="px-6 py-4 font-medium w-[40%]">{t("people.table.member")}</th>
                  <th className="px-6 py-4 font-medium w-[35%]">{t("people.table.email")}</th>
                  <th className="px-6 py-4 font-medium w-[25%] text-center">{t("people.table.role")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/50">
                {paginated.map((user) => (
                  <tr key={user.id} className="hover:bg-zinc-800/40 transition-colors group border-b border-zinc-800/50 last:border-0">
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-full bg-zinc-800 border border-zinc-700/50 flex-shrink-0 overflow-hidden flex items-center justify-center">
                          {user.avatar_url ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img src={user.avatar_url} alt={user.name} className="w-full h-full object-cover" />
                          ) : (
                            <span className="text-xs font-bold text-zinc-400">{user.name.substring(0, 1).toUpperCase()}</span>
                          )}
                        </div>
                        <span className="font-medium text-zinc-200">{user.name}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-zinc-500 font-mono text-xs">
                      {user.email || "-"}
                    </td>
                    <td className="px-6 py-4 text-center">
                      {user.is_admin ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border bg-amber-900/30 text-amber-400 border-amber-800/50">
                          <Shield className="w-3 h-3" />
                          {t("people.role.admin")}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border bg-zinc-800/50 text-zinc-400 border-zinc-700/50">
                          {t("people.role.member")}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {filtered.length > 0 && (
        <div className="mt-4 px-6">
          <PaginationControls
            currentPage={currentPage}
            totalPages={totalPages}
            pageSize={pageSize}
            totalItems={filtered.length}
            onPageChange={setCurrentPage}
            onPageSizeChange={(s) => { setPageSize(s); setCurrentPage(1); }}
          />
        </div>
      )}

      <ConfirmationDialog
        isOpen={showRecruitWip}
        onClose={() => setShowRecruitWip(false)}
        title={t("people.recruitTitle")}
        description={t("people.recruitWip")}
        confirmText={t("common.ok")}
        mode="alert"
      />
    </div>
  );
}

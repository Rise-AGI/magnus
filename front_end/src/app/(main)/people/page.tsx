// front_end/src/app/(main)/people/page.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Search, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { client } from "@/lib/api";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { PeopleTable } from "@/components/people/people-table";
import { PeopleDrawer } from "@/components/people/people-drawer";
import { RecruitDrawer } from "@/components/people/recruit-drawer";
import { GroupInviteDialog } from "@/components/chat/group-invite-dialog";
import { useLanguage } from "@/context/language-context";
import { useDebounce } from "@/hooks/use-debounce";
import { UserDetail } from "@/types/auth";


export default function PeoplePage() {
  const { t } = useLanguage();
  const router = useRouter();
  const [users, setUsers] = useState<UserDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [totalItems, setTotalItems] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebounce(searchQuery);

  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // UI state
  const [selectedUser, setSelectedUser] = useState<UserDetail | null>(null);
  const [showRecruit, setShowRecruit] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<UserDetail | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [inviteTarget, setInviteTarget] = useState<UserDetail | null>(null);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await client(`/api/users/roster?page=${currentPage}&page_size=${pageSize}&search=${encodeURIComponent(debouncedQuery)}`);
      setUsers(res.items);
      setTotalItems(res.total);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [currentPage, pageSize, debouncedQuery]);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);
  useEffect(() => { setCurrentPage(1); }, [debouncedQuery]);

  // Keep selectedUser in sync with latest fetched data
  useEffect(() => {
    if (!selectedUser) return;
    const fresh = users.find((u) => u.id === selectedUser.id);
    if (fresh) setSelectedUser(fresh);
    else setSelectedUser(null);
  }, [users]);  // eslint-disable-line react-hooks/exhaustive-deps

  const totalPages = Math.ceil(totalItems / pageSize);

  const handleDirectChat = async (user: UserDetail) => {
    try {
      const conv = await client("/api/conversations", {
        json: { type: "p2p", member_ids: [user.id] },
      });
      router.push(`/chat/${conv.id}`);
    } catch (e) {
      console.error("Failed to create conversation:", e);
    }
  };

  const handleDeleteAgent = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await client(`/api/users/${deleteTarget.id}`, { method: "DELETE" });
      setDeleteTarget(null);
      fetchUsers();
    } catch (e) {
      console.error(e);
    } finally {
      setIsDeleting(false);
    }
  };

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
          onClick={() => setShowRecruit(true)}
          className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50"
        >
          <Plus className="w-4 h-4" /> {t("people.recruitTitle")}
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

      <PeopleTable
        data={users}
        loading={loading}
        onManage={setSelectedUser}
        onDelete={setDeleteTarget}
        onChat={handleDirectChat}
        onInviteToGroup={setInviteTarget}
      />

      {totalItems > 0 && (
        <div className="mt-4 px-6">
          <PaginationControls
            currentPage={currentPage}
            totalPages={totalPages}
            pageSize={pageSize}
            totalItems={totalItems}
            onPageChange={setCurrentPage}
            onPageSizeChange={(s) => { setPageSize(s); setCurrentPage(1); }}
          />
        </div>
      )}

      <PeopleDrawer
        isOpen={!!selectedUser}
        onClose={() => setSelectedUser(null)}
        user={selectedUser}
        onRefresh={fetchUsers}
      />

      <RecruitDrawer
        isOpen={showRecruit}
        onClose={() => setShowRecruit(false)}
        onSuccess={fetchUsers}
      />

      <ConfirmationDialog
        isOpen={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteAgent}
        title={t("people.drawer.delete")}
        description={t("people.drawer.deleteConfirm")}
        confirmText={t("people.drawer.delete")}
        isLoading={isDeleting}
        variant="danger"
      />

      <GroupInviteDialog
        isOpen={!!inviteTarget}
        onClose={() => setInviteTarget(null)}
        targetUserId={inviteTarget?.id ?? ""}
        targetUserName={inviteTarget?.name ?? ""}
      />
    </div>
  );
}

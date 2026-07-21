// front_end/src/app/(main)/blueprints/page.tsx
"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { Search, Plus } from "lucide-react";
import { client } from "@/lib/api";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { POLL_INTERVAL } from "@/lib/config";
import { DEFAULT_CODE_TEMPLATE } from "@/lib/blueprint-defaults";
import { getUserInitials } from "@/lib/user-display";
import { useLanguage } from "@/context/language-context";
import { useDebounce } from "@/hooks/use-debounce";
import { usePolling } from "@/hooks/use-polling";
import { useUrlPagination } from "@/hooks/use-url-pagination";

import { User } from "@/types/auth";
import { Blueprint } from "@/types/blueprint";

import { BlueprintTable } from "@/components/blueprints/blueprint-table";
import { BlueprintEditor } from "@/components/blueprints/blueprint-editor";
import { BlueprintRunner } from "@/components/blueprints/blueprint-runner";

export default function BlueprintsPage() {
  const { t } = useLanguage();
  const searchParams = useSearchParams();
  const { page, pageSize, setPage, setPageSize, setParams } = useUrlPagination();
  const [blueprints, setBlueprints] = useState<Blueprint[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebounce(searchQuery);
  const [allUsers, setAllUsers] = useState<User[]>([]);
  const selectedUserId = searchParams.get("owner_id") ?? "";

  const [totalItems, setTotalItems] = useState(0);

  const [selectedBlueprint, setSelectedBlueprint] = useState<Blueprint | null>(null);
  const [blueprintToDelete, setBlueprintToDelete] = useState<Blueprint | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [editorMode, setEditorMode] = useState<'create' | 'clone'>('create');
  const [editorData, setEditorData] = useState({ id: "", title: "", description: "", code: DEFAULT_CODE_TEMPLATE });

  useEffect(() => {
    const justCreated = sessionStorage.getItem('magnus_new_blueprint');
    if (justCreated) {
      sessionStorage.removeItem('magnus_new_blueprint');
      setTimeout(() => {
        const main = document.querySelector('main');
        if (main) main.scrollTo({ top: 0, behavior: 'smooth' });
      }, 100);
    }
  }, []);

  useEffect(() => {
    const fetchUsers = async () => { try { const u = await client("/api/users"); setAllUsers(u); } catch (e) { console.error(e); } };
    fetchUsers();
  }, []);

  const userFilterOptions = useMemo(() => [
      { label: t("common.allUsers"), value: "", icon: "/api/logo" },
      ...allUsers.map(u => ({ label: u.name, value: u.id, meta: u.email || "", icon: u.avatar_url || undefined, initials: getUserInitials(u.name) }))
  ], [allUsers, t]);

  // Reset to the first page when the search query changes — but not on mount,
  // which would wipe a page restored from the URL on back-navigation. owner_id
  // changes reset the page atomically in the filter onChange below.
  const isFirstQuery = useRef(true);
  useEffect(() => {
    if (isFirstQuery.current) {
      isFirstQuery.current = false;
      return;
    }
    setParams({ page: null });
  }, [debouncedQuery, setParams]);

  const fetchBlueprints = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const skip = (page - 1) * pageSize;
      const params = new URLSearchParams({ skip: skip.toString(), limit: pageSize.toString() });
      if (debouncedQuery.trim()) params.append("search", debouncedQuery.trim());
      if (selectedUserId) params.append("creator_id", selectedUserId);
      const res = await client(`/api/blueprints?${params.toString()}`);
      setBlueprints(res.items);
      setTotalItems(res.total);
    } catch (e) { console.error(e); } finally { if (!isBackground) setLoading(false); }
  }, [page, pageSize, debouncedQuery, selectedUserId]);

  useEffect(() => { fetchBlueprints(); }, [fetchBlueprints]);
  usePolling(() => fetchBlueprints(true), POLL_INTERVAL);

  // Fill each row's display user from the loaded users list only when the row
  // itself lacks one, derived at render. Keeping allUsers out of fetchBlueprints'
  // deps is what matters: otherwise the async users fetch recreates fetchBlueprints
  // and retriggers a foreground reload, flashing the loading state a second time.
  const displayedBlueprints = useMemo(
    () => blueprints.map(b => ({ ...b, user: b.user || allUsers.find(u => u.id === b.user_id) })),
    [blueprints, allUsers],
  );

  const handleOpenRun = (bp: Blueprint) => setSelectedBlueprint(bp);
  
  const handleClone = async (bp: Blueprint) => {
      // 列表投影不含 code（后端 BlueprintListItem 省掉了可能几十 MB 的它），
      // 点击克隆时按需拉完整蓝图再回填编辑器；正常蓝图近乎无感。
      let full = bp;
      try {
        full = await client(`/api/blueprints/${bp.id}`);
      } catch (e) {
        console.error("Failed to load blueprint detail for clone", e);
      }
      setEditorData({ id: full.id, title: full.title, description: full.description, code: full.code ?? "" });
      setEditorMode('clone');
      setIsEditorOpen(true);
  };
  
  const handleDelete = async () => {
    if (!blueprintToDelete) return;
    setIsDeleting(true);
    try {
      await client(`/api/blueprints/${blueprintToDelete.id}`, { method: "DELETE" });
      fetchBlueprints();
      setBlueprintToDelete(null);
    } catch (e: any) {
      setErrorMessage(e.message || t("common.operationFailed"));
    } finally {
      setIsDeleting(false);
    }
  };
  
  const handleSave = async (data: any) => {
    await client("/api/blueprints", { method: "POST", json: data });
    const main = document.querySelector('main');
    if (main) main.scrollTo({ top: 0, behavior: 'smooth' });
    fetchBlueprints();
  };

  return (
    <div className="relative min-h-[calc(100vh-8rem)] pb-20">
      <style jsx global>{`
          .prism-editor textarea { outline: none !important; }
          code[class*="language-"], pre[class*="language-"] { text-shadow: none !important; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace !important; }
          ::-webkit-scrollbar { display: none; }
          html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      <div className="flex flex-col sm:flex-row items-start sm:items-center sm:justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">{t("nav.blueprints")}</h1>
          <p className="text-zinc-500 text-sm mt-1">{t("blueprints.subtitle")}</p>
        </div>
        <button onClick={() => { setEditorData({ id: "", title: "", description: "", code: DEFAULT_CODE_TEMPLATE }); setEditorMode('create'); setIsEditorOpen(true); }} className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50">
            <Plus className="w-4 h-4"/> {t("blueprints.new")}
        </button>
      </div>

      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-1.5 mb-6 flex flex-wrap items-center gap-2 backdrop-blur-sm relative z-20">
        <div className="relative flex-1 group">
           <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 group-focus-within:text-blue-500 transition-colors" />
           <input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder={t("blueprints.searchPlaceholder")} className="w-full bg-transparent border-none py-2.5 pl-9 pr-4 text-sm text-zinc-200 focus:outline-none focus:ring-0 placeholder-zinc-600" />
        </div>
        <div className="h-6 w-px bg-zinc-800 hidden sm:block"></div>
        <div className="w-full sm:w-56">
          <SearchableSelect value={selectedUserId} onChange={(uid) => setParams({ owner_id: uid || null, page: null })} options={userFilterOptions} placeholder={t("blueprints.filterByUser")} className="mb-0 border-none bg-transparent" />
        </div>
      </div>

      <BlueprintTable
        data={displayedBlueprints} loading={loading} onRun={handleOpenRun} onClone={handleClone} onDelete={setBlueprintToDelete} onRefresh={() => fetchBlueprints(true)}
      />
      {displayedBlueprints.length > 0 && (
        <div className="mt-4 px-6">
          <PaginationControls currentPage={page} totalPages={Math.ceil(totalItems / pageSize)} pageSize={pageSize} totalItems={totalItems} onPageChange={setPage} onPageSizeChange={setPageSize} />
        </div>
      )}

      <BlueprintEditor isOpen={isEditorOpen} mode={editorMode} initialData={editorData} onClose={() => setIsEditorOpen(false)} onSave={handleSave} />
      <BlueprintRunner blueprint={selectedBlueprint} onClose={() => setSelectedBlueprint(null)} />
      <ConfirmationDialog isOpen={!!blueprintToDelete} onClose={() => setBlueprintToDelete(null)} onConfirm={handleDelete} title={t("blueprints.deleteTitle")} description={<span>{t("blueprints.deleteConfirm", { title: blueprintToDelete?.title || "" })}</span>} confirmText={t("common.delete")} variant="danger" isLoading={isDeleting} confirmInput={blueprintToDelete?.id} />
      <ConfirmationDialog isOpen={!!errorMessage} onClose={() => setErrorMessage(null)} title={t("common.error")} description={errorMessage} confirmText={t("common.ok")} mode="alert" variant="danger" />
    </div>
  );
}

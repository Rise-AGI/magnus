// front_end/src/app/(main)/blueprints/page.tsx
"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Search, DraftingCompass } from "lucide-react";
import { client } from "@/lib/api";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { POLL_INTERVAL } from "@/lib/config";
import { DEFAULT_CODE_TEMPLATE } from "@/lib/blueprint-defaults";

import { User } from "@/types/auth";
import { Blueprint } from "@/types/blueprint";

import { BlueprintTable } from "@/components/blueprints/blueprint-table";
import { BlueprintEditor } from "@/components/blueprints/blueprint-editor";
import { BlueprintRunner } from "@/components/blueprints/blueprint-runner";

export default function BlueprintsPage() {
  const [blueprints, setBlueprints] = useState<Blueprint[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [allUsers, setAllUsers] = useState<User[]>([]);
  const [selectedUserId, setSelectedUserId] = useState("");
  
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [totalItems, setTotalItems] = useState(0);

  const [selectedBlueprint, setSelectedBlueprint] = useState<Blueprint | null>(null);
  const [blueprintToDelete, setBlueprintToDelete] = useState<Blueprint | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [editorMode, setEditorMode] = useState<'create' | 'clone'>('create');
  const [editorData, setEditorData] = useState({ id: "", title: "", description: "", code: DEFAULT_CODE_TEMPLATE });
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    const fetchUsers = async () => { try { const u = await client("/api/users"); setAllUsers(u); } catch (e) { console.error(e); } };
    fetchUsers();
  }, []);

  const userFilterOptions = useMemo(() => [
      { label: "All Users", value: "", icon: "/api/logo" },
      ...allUsers.map(u => ({ label: u.name, value: u.id, meta: u.email || "", icon: u.avatar_url || undefined }))
  ], [allUsers]);

  useEffect(() => { const t = setTimeout(() => setDebouncedQuery(searchQuery), 300); return () => clearTimeout(t); }, [searchQuery]);
  useEffect(() => { setCurrentPage(1); }, [debouncedQuery, selectedUserId]);

  const fetchBlueprints = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const skip = (currentPage - 1) * pageSize;
      const params = new URLSearchParams({ skip: skip.toString(), limit: pageSize.toString() });
      if (debouncedQuery.trim()) params.append("search", debouncedQuery.trim());
      if (selectedUserId) params.append("creator_id", selectedUserId);
      const res = await client(`/api/blueprints?${params.toString()}`);
      
      // Data Mapping: Backend (snake_case) -> Frontend Interface (camelCase)
      setBlueprints(res.items.map((b: any) => ({ 
          ...b, 
          updatedAt: b.updated_at || b.updatedAt, // Handle backend inconsistency here, not in Type
          user: b.user || allUsers.find(u => u.id === b.user_id) 
      })));
      setTotalItems(res.total);
    } catch (e) { console.error(e); } finally { if (!isBackground) setLoading(false); }
  }, [currentPage, pageSize, debouncedQuery, selectedUserId, allUsers]);

  useEffect(() => { fetchBlueprints(); const i = setInterval(() => fetchBlueprints(true), POLL_INTERVAL); return () => clearInterval(i); }, [fetchBlueprints]);

  const handleOpenRun = (bp: Blueprint) => setSelectedBlueprint(bp);
  
  const handleClone = (bp: Blueprint) => {
      setEditorData({ id: bp.id, title: bp.title, description: bp.description, code: bp.code });
      setEditorMode('clone');
      setIsEditorOpen(true);
  };
  
  const handleDelete = async () => {
    if (!blueprintToDelete) return;
    setIsDeleting(true);
    try { await client(`/api/blueprints/${blueprintToDelete.id}`, { method: "DELETE" }); fetchBlueprints(); setBlueprintToDelete(null); }
    catch (e: any) { alert(e.message); } finally { setIsDeleting(false); }
  };
  
  const handleSave = async (data: any) => {
    setIsSaving(true);
    try { await client("/api/blueprints", { method: "POST", json: data }); setIsEditorOpen(false); fetchBlueprints(); }
    catch (e: any) { alert(e.message); } finally { setIsSaving(false); }
  };

  return (
    <div className="relative min-h-[calc(100vh-8rem)] pb-20">
      <style jsx global>{`
          .prism-editor textarea { outline: none !important; }
          code[class*="language-"], pre[class*="language-"] { text-shadow: none !important; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace !important; }
          ::-webkit-scrollbar { display: none; }
          html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">Blueprints Registry</h1>
          <p className="text-zinc-500 text-sm mt-1">Standardized task templates via Python-defined logic.</p>
        </div>
        <button onClick={() => { setEditorData({ id: "", title: "", description: "", code: DEFAULT_CODE_TEMPLATE }); setEditorMode('create'); setIsEditorOpen(true); }} className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50">
            <DraftingCompass className="w-4 h-4"/> New Blueprint
        </button>
      </div>

      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-1.5 mb-6 flex items-center gap-2 backdrop-blur-sm relative z-20">
        <div className="relative flex-1 group">
           <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 group-focus-within:text-blue-500 transition-colors" />
           <input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Search Blueprints..." className="w-full bg-transparent border-none py-2.5 pl-9 pr-4 text-sm text-zinc-200 focus:outline-none focus:ring-0 placeholder-zinc-600" />
        </div>
        <div className="h-6 w-px bg-zinc-800"></div>
        <div className="w-56"> 
          <SearchableSelect value={selectedUserId} onChange={setSelectedUserId} options={userFilterOptions} placeholder="Filter by User" className="mb-0 border-none bg-transparent" />
        </div>
      </div>

      <BlueprintTable 
        data={blueprints} loading={loading} onRun={handleOpenRun} onClone={handleClone} onDelete={setBlueprintToDelete} 
      />
      {blueprints.length > 0 && (
        <div className="mt-4 px-6">
          <PaginationControls currentPage={currentPage} totalPages={Math.ceil(totalItems / pageSize)} pageSize={pageSize} totalItems={totalItems} onPageChange={setCurrentPage} onPageSizeChange={(s) => { setPageSize(s); setCurrentPage(1); }} />
        </div>
      )}

      <BlueprintEditor isOpen={isEditorOpen} mode={editorMode} initialData={editorData} onClose={() => setIsEditorOpen(false)} onSave={handleSave} isSaving={isSaving} />
      <BlueprintRunner blueprint={selectedBlueprint} onClose={() => setSelectedBlueprint(null)} />
      <ConfirmationDialog isOpen={!!blueprintToDelete} onClose={() => setBlueprintToDelete(null)} onConfirm={handleDelete} title="Delete Blueprint" description={<span>Are you sure you want to delete blueprint <strong>{blueprintToDelete?.title}</strong>?</span>} confirmText="Delete" variant="danger" isLoading={isDeleting} />
    </div>
  );
}
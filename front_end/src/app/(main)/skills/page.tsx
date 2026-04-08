// front_end/src/app/(main)/skills/page.tsx
"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Search, Plus } from "lucide-react";
import { client } from "@/lib/api";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { POLL_INTERVAL } from "@/lib/config";
import { getUserInitials } from "@/lib/user-display";
import { useLanguage } from "@/context/language-context";
import { useDebounce } from "@/hooks/use-debounce";

import { User } from "@/types/auth";
import { Skill } from "@/types/skill";

import { SkillTable } from "@/components/skills/skill-table";
import { SkillEditor, DEFAULT_EDITOR_DATA } from "@/components/skills/skill-editor";

export default function SkillsPage() {
  const { t } = useLanguage();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebounce(searchQuery);
  const [allUsers, setAllUsers] = useState<User[]>([]);
  const [selectedUserId, setSelectedUserId] = useState("");

  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [totalItems, setTotalItems] = useState(0);

  const [skillToDelete, setSkillToDelete] = useState<Skill | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [editorMode, setEditorMode] = useState<"create" | "clone">("create");
  const [editorData, setEditorData] = useState(DEFAULT_EDITOR_DATA);

  useEffect(() => {
    const fetchUsers = async () => { try { const u = await client("/api/users"); setAllUsers(u); } catch (e) { console.error(e); } };
    fetchUsers();
  }, []);

  const userFilterOptions = useMemo(() => [
      { label: t("common.allUsers"), value: "", icon: "/api/logo" },
      ...allUsers.map(u => ({ label: u.name, value: u.id, meta: u.email || "", icon: u.avatar_url || undefined, initials: getUserInitials(u.name) }))
  ], [allUsers, t]);

  useEffect(() => { setCurrentPage(1); }, [debouncedQuery, selectedUserId]);

  const fetchSkills = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const skip = (currentPage - 1) * pageSize;
      const params = new URLSearchParams({ skip: skip.toString(), limit: pageSize.toString() });
      if (debouncedQuery.trim()) params.append("search", debouncedQuery.trim());
      if (selectedUserId) params.append("creator_id", selectedUserId);
      const res = await client(`/api/skills?${params.toString()}`);

      setSkills(res.items.map((s: any) => ({
          ...s,
          created_at: s.created_at,
          updated_at: s.updated_at,
          user: s.user || allUsers.find(u => u.id === s.user_id)
      })));
      setTotalItems(res.total);
    } catch (e) { console.error(e); } finally { if (!isBackground) setLoading(false); }
  }, [currentPage, pageSize, debouncedQuery, selectedUserId, allUsers]);

  useEffect(() => { fetchSkills(); const i = setInterval(() => fetchSkills(true), POLL_INTERVAL); return () => clearInterval(i); }, [fetchSkills]);

  const handleClone = (skill: Skill) => {
      setEditorData({
        id: skill.id,
        title: skill.title,
        description: skill.description,
        files: skill.files.map(f => ({ path: f.path, content: f.content })),
      });
      setEditorMode("clone");
      setIsEditorOpen(true);
  };

  const handleDelete = async () => {
    if (!skillToDelete) return;
    setIsDeleting(true);
    try {
      await client(`/api/skills/${skillToDelete.id}`, { method: "DELETE" });
      fetchSkills();
      setSkillToDelete(null);
    } catch (e: any) {
      setErrorMessage(e.message || t("common.operationFailed"));
    } finally {
      setIsDeleting(false);
    }
  };

  const handleSave = async (data: any) => {
    await client("/api/skills", { method: "POST", json: data });
    const main = document.querySelector("main");
    if (main) main.scrollTo({ top: 0, behavior: "smooth" });
    fetchSkills();
  };

  return (
    <div className="relative min-h-[calc(100vh-8rem)] pb-20">
      <style jsx global>{`
          ::-webkit-scrollbar { display: none; }
          html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      <div className="flex flex-col sm:flex-row items-start sm:items-center sm:justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">{t("nav.skills")}</h1>
          <p className="text-zinc-500 text-sm mt-1">{t("skills.subtitle")}</p>
        </div>
        <button onClick={() => { setEditorData(DEFAULT_EDITOR_DATA); setEditorMode("create"); setIsEditorOpen(true); }} className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50">
            <Plus className="w-4 h-4"/> {t("skills.new")}
        </button>
      </div>

      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-1.5 mb-6 flex flex-wrap items-center gap-2 backdrop-blur-sm relative z-20">
        <div className="relative flex-1 group">
           <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 group-focus-within:text-blue-500 transition-colors" />
           <input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder={t("skills.searchPlaceholder")} className="w-full bg-transparent border-none py-2.5 pl-9 pr-4 text-sm text-zinc-200 focus:outline-none focus:ring-0 placeholder-zinc-600" />
        </div>
        <div className="h-6 w-px bg-zinc-800 hidden sm:block"></div>
        <div className="w-full sm:w-56">
          <SearchableSelect value={selectedUserId} onChange={setSelectedUserId} options={userFilterOptions} placeholder={t("skills.filterByUser")} className="mb-0 border-none bg-transparent" />
        </div>
      </div>

      <SkillTable
        data={skills} loading={loading} onClone={handleClone} onDelete={setSkillToDelete} onRefresh={() => fetchSkills(true)}
      />
      {skills.length > 0 && (
        <div className="mt-4 px-6">
          <PaginationControls currentPage={currentPage} totalPages={Math.ceil(totalItems / pageSize)} pageSize={pageSize} totalItems={totalItems} onPageChange={setCurrentPage} onPageSizeChange={(s) => { setPageSize(s); setCurrentPage(1); }} />
        </div>
      )}

      <SkillEditor isOpen={isEditorOpen} mode={editorMode} initialData={editorData} onClose={() => setIsEditorOpen(false)} onSave={handleSave} />
      <ConfirmationDialog isOpen={!!skillToDelete} onClose={() => setSkillToDelete(null)} onConfirm={handleDelete} title={t("skills.deleteTitle")} description={<span>{t("skills.deleteConfirm", { title: skillToDelete?.title || "" })}</span>} confirmText={t("common.delete")} variant="danger" isLoading={isDeleting} confirmInput={skillToDelete?.id} />
      <ConfirmationDialog isOpen={!!errorMessage} onClose={() => setErrorMessage(null)} title={t("common.error")} description={errorMessage} confirmText={t("common.ok")} mode="alert" variant="danger" />
    </div>
  );
}

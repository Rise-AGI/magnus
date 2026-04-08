// front_end/src/app/(main)/images/page.tsx
"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Search, Plus, Container, Loader2, RefreshCw, Clock } from "lucide-react";
import { client } from "@/lib/api";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { Drawer } from "@/components/ui/drawer";
import { AvatarCircle } from "@/components/ui/user-avatar";
import { POLL_INTERVAL } from "@/lib/config";
import { getUserInitials } from "@/lib/user-display";
import { useLanguage } from "@/context/language-context";
import { useDebounce } from "@/hooks/use-debounce";
import { formatBeijingTime } from "@/lib/utils";

import { User } from "@/types/auth";

import { ImageTable, CachedImage, formatSize, extractImageName, STATUS_STYLES, STATUS_I18N, isBusy } from "@/components/images/image-table";

export default function ImagesPage() {
  const { t } = useLanguage();
  const [images, setImages] = useState<CachedImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebounce(searchQuery);
  const [allUsers, setAllUsers] = useState<User[]>([]);
  const [selectedUserId, setSelectedUserId] = useState("");

  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [totalItems, setTotalItems] = useState(0);

  const [imageToDelete, setImageToDelete] = useState<CachedImage | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Preheat drawer
  const [isPreheatOpen, setIsPreheatOpen] = useState(false);
  const [preheatUri, setPreheatUri] = useState("");
  const [isPreheating, setIsPreheating] = useState(false);
  const [preheatError, setPreheatError] = useState<string | null>(null);

  // Detail drawer (view + refresh)
  const [viewingImage, setViewingImage] = useState<CachedImage | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  useEffect(() => {
    const fetchUsers = async () => { try { const u = await client("/api/users"); setAllUsers(u); } catch (e) { console.error(e); } };
    fetchUsers();
  }, []);

  const userFilterOptions = useMemo(() => [
    { label: t("common.allUsers"), value: "", icon: "/api/logo" },
    ...allUsers.map(u => ({ label: u.name, value: u.id, meta: u.email || "", icon: u.avatar_url || undefined, initials: getUserInitials(u.name) }))
  ], [allUsers, t]);

  useEffect(() => { setCurrentPage(1); }, [debouncedQuery, selectedUserId]);

  const fetchImages = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const skip = (currentPage - 1) * pageSize;
      const params = new URLSearchParams({ skip: skip.toString(), limit: pageSize.toString() });
      if (debouncedQuery.trim()) params.append("search", debouncedQuery.trim());
      if (selectedUserId) params.append("owner_id", selectedUserId);
      const res = await client(`/api/images?${params.toString()}`);
      setImages(res.items);
      setTotalItems(res.total);
    } catch (e) {
      console.error(e);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [currentPage, pageSize, debouncedQuery, selectedUserId]);

  useEffect(() => {
    fetchImages();
    const i = setInterval(() => fetchImages(true), POLL_INTERVAL);
    return () => clearInterval(i);
  }, [fetchImages]);

  // Preheat
  const openPreheat = () => {
    setPreheatUri("");
    setPreheatError(null);
    setIsPreheating(false);
    setIsPreheatOpen(true);
  };

  const closePreheat = () => {
    if (isPreheating) return;
    setIsPreheatOpen(false);
  };

  const handlePreheat = async () => {
    const uri = preheatUri.trim();
    if (!uri) {
      setPreheatError(t("images.uriRequired"));
      return;
    }
    setPreheatError(null);
    setIsPreheating(true);
    try {
      await client("/api/images", { method: "POST", json: { uri } });
      setIsPreheatOpen(false);
      fetchImages();
    } catch (e: any) {
      setPreheatError(e.message || t("common.operationFailed"));
    } finally {
      setIsPreheating(false);
    }
  };

  // View + Refresh
  const closeDetail = () => {
    if (isRefreshing) return;
    setViewingImage(null);
  };

  const handleRefresh = async () => {
    if (!viewingImage?.id) return;
    setIsRefreshing(true);
    try {
      await client(`/api/images/${viewingImage.id}/refresh`, { method: "POST" });
      setViewingImage(null);
      fetchImages();
    } catch (e: any) {
      setErrorMessage(e.message || t("common.operationFailed"));
    } finally {
      setIsRefreshing(false);
    }
  };

  // Delete
  const handleDelete = async () => {
    if (!imageToDelete || !imageToDelete.id) return;
    setIsDeleting(true);
    try {
      await client(`/api/images/${imageToDelete.id}`, { method: "DELETE" });
      fetchImages();
      setImageToDelete(null);
    } catch (e: any) {
      setErrorMessage(e.message || t("common.operationFailed"));
    } finally {
      setIsDeleting(false);
    }
  };

  const detailBusy = viewingImage ? isBusy(viewingImage.status) : false;
  const detailStatusKey = viewingImage ? STATUS_I18N[viewingImage.status] as any : undefined;
  const detailStatusLabel = detailStatusKey ? t(detailStatusKey) : viewingImage?.status;

  return (
    <div className="relative min-h-[calc(100vh-8rem)] pb-20">
      <style jsx global>{`
        ::-webkit-scrollbar { display: none; }
        html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      <div className="flex flex-col sm:flex-row items-start sm:items-center sm:justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">{t("nav.images")}</h1>
          <p className="text-zinc-500 text-sm mt-1">{t("images.subtitle")}</p>
        </div>
        <button
          onClick={openPreheat}
          className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50"
        >
          <Plus className="w-4 h-4" /> {t("images.preheat")}
        </button>
      </div>

      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-1.5 mb-6 flex flex-wrap items-center gap-2 backdrop-blur-sm relative z-20">
        <div className="relative flex-1 group">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 group-focus-within:text-blue-500 transition-colors" />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t("images.searchPlaceholder")}
            className="w-full bg-transparent border-none py-2.5 pl-9 pr-4 text-sm text-zinc-200 focus:outline-none focus:ring-0 placeholder-zinc-600"
          />
        </div>
        <div className="h-6 w-px bg-zinc-800 hidden sm:block"></div>
        <div className="w-full sm:w-56">
          <SearchableSelect value={selectedUserId} onChange={setSelectedUserId} options={userFilterOptions} placeholder={t("images.filterByUser")} className="mb-0 border-none bg-transparent" />
        </div>
      </div>

      <ImageTable data={images} loading={loading} onView={setViewingImage} onDelete={setImageToDelete} onRefresh={() => fetchImages(true)} />

      {images.length > 0 && (
        <div className="mt-4 px-6">
          <PaginationControls
            currentPage={currentPage}
            totalPages={Math.ceil(totalItems / pageSize)}
            pageSize={pageSize}
            totalItems={totalItems}
            onPageChange={setCurrentPage}
            onPageSizeChange={(s) => { setPageSize(s); setCurrentPage(1); }}
          />
        </div>
      )}

      {/* Preheat Drawer */}
      <Drawer
        isOpen={isPreheatOpen}
        onClose={closePreheat}
        title={t("images.preheatTitle")}
        icon={<Container className="w-5 h-5 text-blue-500" />}
      >
        <div className="flex flex-col min-h-full">
          <div className="flex-1">
            <div>
              <label className={`text-xs uppercase tracking-wider mb-1.5 block font-medium ${preheatError ? "text-red-500" : "text-zinc-500"}`}>
                {t("images.uri")} <span className="text-red-500">*</span>
              </label>
              <input
                value={preheatUri}
                onChange={(e) => { setPreheatUri(e.target.value); setPreheatError(null); }}
                placeholder="docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"
                className={`w-full bg-zinc-950 border px-4 py-2.5 rounded-lg text-zinc-200 text-sm font-mono focus:border-blue-500 outline-none transition-all placeholder-zinc-700
                  ${preheatError ? "animate-shake border-red-500" : "border-zinc-800"}`}
                disabled={isPreheating}
                onKeyDown={(e) => { if (e.key === "Enter") handlePreheat(); }}
                autoFocus
              />
            </div>
          </div>

          <div className="mt-auto pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4 pb-1">
            {preheatError && (
              <span className="text-red-500 text-xs font-bold animate-pulse">{preheatError}</span>
            )}
            <div className="flex gap-3 w-full sm:w-auto sm:ml-auto">
              <button onClick={closePreheat} disabled={isPreheating} className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors disabled:opacity-50">{t("common.cancel")}</button>
              <button
                onClick={handlePreheat}
                disabled={isPreheating || !preheatUri.trim()}
                className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isPreheating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Container className="w-4 h-4" />}
                {isPreheating ? t("images.preheating") : t("images.preheat")}
              </button>
            </div>
          </div>
        </div>
      </Drawer>

      {/* Detail / Refresh Drawer */}
      <Drawer
        isOpen={!!viewingImage}
        onClose={closeDetail}
        title={t("images.detailTitle")}
        icon={<Container className="w-5 h-5 text-blue-500" />}
      >
        {viewingImage && (
          <div className="flex flex-col min-h-full">
            <div className="flex-1 space-y-5">

              {/* URI */}
              <div>
                <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">URI</label>
                <div className="bg-zinc-950 border border-zinc-800 px-4 py-2.5 rounded-lg text-zinc-200 text-sm font-mono break-all">
                  {viewingImage.uri}
                </div>
              </div>

              {/* Status + Size */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">{t("images.table.status")}</label>
                  <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${STATUS_STYLES[viewingImage.status] || "bg-zinc-800 text-zinc-400 border-zinc-700"}`}>
                    {detailBusy && <Loader2 className="w-3 h-3 animate-spin" />}
                    {detailStatusLabel}
                  </span>
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">{t("images.table.size")}</label>
                  <span className="text-sm text-zinc-200 font-mono">{formatSize(viewingImage.size_bytes)}</span>
                </div>
              </div>

              {/* Owner */}
              <div>
                <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">{t("images.table.owner")}</label>
                <div className="flex items-center gap-3 mt-1">
                  {viewingImage.user ? (
                    <>
                      <AvatarCircle user={viewingImage.user} size="sm" />
                      <span className="text-sm font-medium text-zinc-200">{viewingImage.user.name}</span>
                    </>
                  ) : (
                    <span className="text-sm text-zinc-500">-</span>
                  )}
                </div>
              </div>

              {/* Timestamps */}
              <div className="grid grid-cols-2 gap-4">
                {viewingImage.created_at && (
                  <div>
                    <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">{t("images.detail.created")}</label>
                    <span className="text-sm text-zinc-400 font-mono flex items-center gap-1.5">
                      <Clock className="w-3.5 h-3.5" />
                      {formatBeijingTime(viewingImage.created_at)}
                    </span>
                  </div>
                )}
                {viewingImage.updated_at && (
                  <div>
                    <label className="text-xs uppercase tracking-wider mb-1.5 block font-medium text-zinc-500">{t("images.detail.updated")}</label>
                    <span className="text-sm text-zinc-400 font-mono flex items-center gap-1.5">
                      <Clock className="w-3.5 h-3.5" />
                      {formatBeijingTime(viewingImage.updated_at)}
                    </span>
                  </div>
                )}
              </div>

            </div>

            <div className="mt-auto pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4 pb-1">
              <div className="flex gap-3 w-full sm:w-auto sm:ml-auto">
                <button onClick={closeDetail} disabled={isRefreshing} className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors disabled:opacity-50">{t("common.cancel")}</button>
                <button
                  onClick={handleRefresh}
                  disabled={isRefreshing || detailBusy || !viewingImage.can_manage}
                  className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isRefreshing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                  {t("images.refresh")}
                </button>
              </div>
            </div>
          </div>
        )}
      </Drawer>

      {/* Delete Confirmation */}
      <ConfirmationDialog
        isOpen={!!imageToDelete}
        onClose={() => setImageToDelete(null)}
        onConfirm={handleDelete}
        title={t("images.deleteTitle")}
        description={<span>{t("images.deleteConfirm", { uri: imageToDelete?.uri || "" })}</span>}
        confirmText={t("common.delete")}
        variant="danger"
        isLoading={isDeleting}
        confirmInput={imageToDelete ? extractImageName(imageToDelete.uri) : undefined}
      />

      {/* Error Dialog */}
      <ConfirmationDialog
        isOpen={!!errorMessage}
        onClose={() => setErrorMessage(null)}
        title={t("common.error")}
        description={errorMessage}
        confirmText={t("common.ok")}
        mode="alert"
        variant="danger"
      />
    </div>
  );
}

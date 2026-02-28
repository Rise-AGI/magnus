// front_end/src/app/(main)/images/page.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Search, Plus, Container, Loader2 } from "lucide-react";
import { client } from "@/lib/api";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { Drawer } from "@/components/ui/drawer";
import { POLL_INTERVAL } from "@/lib/config";
import { useLanguage } from "@/context/language-context";
import { useDebounce } from "@/hooks/use-debounce";

import { ImageTable, CachedImage } from "@/components/images/image-table";

export default function ImagesPage() {
  const { t } = useLanguage();
  const [images, setImages] = useState<CachedImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebounce(searchQuery);

  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [totalItems, setTotalItems] = useState(0);

  const [imageToDelete, setImageToDelete] = useState<CachedImage | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Preheat drawer
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [preheatUri, setPreheatUri] = useState("");
  const [isPreheating, setIsPreheating] = useState(false);
  const [preheatError, setPreheatError] = useState<string | null>(null);

  useEffect(() => { setCurrentPage(1); }, [debouncedQuery]);

  const fetchImages = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const skip = (currentPage - 1) * pageSize;
      const params = new URLSearchParams({ skip: skip.toString(), limit: pageSize.toString() });
      if (debouncedQuery.trim()) params.append("search", debouncedQuery.trim());
      const res = await client(`/api/images?${params.toString()}`);
      setImages(res.items);
      setTotalItems(res.total);
    } catch (e) {
      console.error(e);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [currentPage, pageSize, debouncedQuery]);

  useEffect(() => {
    fetchImages();
    const i = setInterval(() => fetchImages(true), POLL_INTERVAL);
    return () => clearInterval(i);
  }, [fetchImages]);

  const openDrawer = () => {
    setPreheatUri("");
    setPreheatError(null);
    setIsPreheating(false);
    setIsDrawerOpen(true);
  };

  const closeDrawer = () => {
    if (isPreheating) return;
    setIsDrawerOpen(false);
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
      setIsDrawerOpen(false);
      fetchImages();
    } catch (e: any) {
      setPreheatError(e.message || t("common.operationFailed"));
    } finally {
      setIsPreheating(false);
    }
  };

  const handleRefresh = async (image: CachedImage) => {
    if (!image.id) return;
    try {
      await client(`/api/images/${image.id}/refresh`, { method: "POST" });
      fetchImages();
    } catch (e: any) {
      setErrorMessage(e.message || t("common.operationFailed"));
    }
  };

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

  return (
    <div className="relative min-h-[calc(100vh-8rem)] pb-20">
      <style jsx global>{`
        ::-webkit-scrollbar { display: none; }
        html { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>

      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">{t("nav.images")}</h1>
          <p className="text-zinc-500 text-sm mt-1">{t("images.subtitle")}</p>
        </div>
        <button
          onClick={openDrawer}
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
      </div>

      <ImageTable data={images} loading={loading} onRefresh={handleRefresh} onDelete={setImageToDelete} />

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
        isOpen={isDrawerOpen}
        onClose={closeDrawer}
        title={t("images.preheatTitle")}
        icon={<Container className="w-5 h-5 text-blue-500" />}
      >
        <div className="flex flex-col min-h-full">
          <div className="flex-1 space-y-6">
            <div className="max-w-lg mx-auto space-y-6">
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
                <p className="text-[10px] text-zinc-600 mt-1">{t("images.uriHint")}</p>
              </div>
            </div>
          </div>

          <div className="mt-auto pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4 pb-1">
            {preheatError ? (
              <span className="text-red-500 text-xs font-bold animate-pulse">{preheatError}</span>
            ) : (
              <span className="text-zinc-500 text-xs hidden sm:block">{t("images.uriHint")}</span>
            )}
            <div className="flex gap-3 w-full sm:w-auto">
              <button onClick={closeDrawer} disabled={isPreheating} className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors disabled:opacity-50">{t("common.cancel")}</button>
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

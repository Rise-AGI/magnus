// front_end/src/app/(main)/services/page.tsx
"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { Search, Plus, Activity, ArrowUpDown } from "lucide-react";
import { client } from "@/lib/api";
import { POLL_INTERVAL } from "@/lib/config";
import { Service } from "@/types/service";
import { User } from "@/types/auth";
import { useLanguage } from "@/context/language-context";
import { useDebounce } from "@/hooks/use-debounce";
import { usePolling } from "@/hooks/use-polling";
import { useUrlPagination } from "@/hooks/use-url-pagination";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { ServiceTable } from "@/components/services/service-table";
import { ServiceDrawer } from "@/components/services/service-drawer";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { getUserInitials } from "@/lib/user-display";

export default function ServicesPage() {
  const { t } = useLanguage();
  const searchParams = useSearchParams();
  const { page, pageSize, setPage, setPageSize, setParams } = useUrlPagination();

  // 排序选项配置
  const SORT_OPTIONS = [
    { label: t("services.sortLastActive"), value: "activity", meta: t("services.sortTraffic") },
    { label: t("services.sortUpdated"), value: "updated", meta: t("services.sortConfig") },
  ];

  const [services, setServices] = useState<Service[]>([]);
  const [allUsers, setAllUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters & Sorting
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebounce(searchQuery);
  // owner_id lives in the URL so owner links and back-navigation round-trip it.
  const selectedUserId = searchParams.get("owner_id") ?? "";
  // [Magnus Update] 新增筛选和排序状态
  const [activeOnly, setActiveOnly] = useState(false);
  const [sortBy, setSortBy] = useState<string>("activity");

  const [totalItems, setTotalItems] = useState(0);

  // Drawer State
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [editingService, setEditingService] = useState<Service | null>(null);

  // Confirmation Dialog State
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<{
    type: "delete" | "toggle";
    service: Service;
  } | null>(null);

  const skip = (page - 1) * pageSize;

  // 1. Fetch Services
  const fetchServices = useCallback(async (isBackground = false) => {
    if (!isBackground) setLoading(true);
    try {
      const params = new URLSearchParams({
        skip: skip.toString(),
        limit: pageSize.toString(),
      });
      if (debouncedQuery.trim()) params.append("search", debouncedQuery.trim());
      if (selectedUserId) params.append("owner_id", selectedUserId);
      
      // [Magnus Update] 传递新的筛选参数
      if (activeOnly) params.append("active_only", "true");
      params.append("sort_by", sortBy);

      const data = await client(`/api/services?${params.toString()}`);
      setServices(data.items);
      setTotalItems(data.total);
    } catch (e) {
      console.error("Backend offline?", e);
    } finally {
      if (!isBackground) setLoading(false);
    }
  }, [skip, pageSize, debouncedQuery, selectedUserId, activeOnly, sortBy]);

  // 2. Fetch Users
  useEffect(() => {
    const fetchUsers = async () => {
      try {
        const users = await client("/api/users");
        setAllUsers(users);
      } catch (e) {
        console.error("Failed to load users list", e);
      }
    };
    fetchUsers();
  }, []);

  const userFilterOptions = useMemo(() => {
    return [
      { label: t("common.allUsers"), value: "", icon: "/api/logo" },
      ...allUsers.map((u) => ({
        label: u.name,
        value: u.id,
        meta: u.email || "",
        icon: u.avatar_url || undefined,
        initials: getUserInitials(u.name),
      })),
    ];
  }, [allUsers, t]);

  // Reset to the first page when search / activeOnly / sortBy change — but not
  // on mount, which would wipe a page restored from the URL on back-navigation.
  // owner_id changes reset the page atomically in the filter onChange below.
  const isFirstFilter = useRef(true);
  useEffect(() => {
    if (isFirstFilter.current) {
      isFirstFilter.current = false;
      return;
    }
    setParams({ page: null });
  }, [debouncedQuery, activeOnly, sortBy, setParams]);

  useEffect(() => { fetchServices(); }, [fetchServices]);
  usePolling(() => fetchServices(true), POLL_INTERVAL);

  // Handlers
  const handleCreate = () => {
    setEditingService(null);
    setIsDrawerOpen(true);
  };

  const handleClone = async (svc: Service) => {
    // 列表投影不含 entry_command / system_entry_command（后端 ServiceListItem 省掉了），
    // 点击克隆时按需拉完整 service 再回填表单。拉详情失败就中止并报错（与 toggle 一致），
    // 不用残缺的列表对象打开一个没有命令的克隆表单。
    let full: Service;
    try {
      full = await client(`/api/services/${svc.id}`);
    } catch (e) {
      console.error("Failed to load service detail for clone", e);
      setErrorMessage(t("common.operationFailed"));
      return;
    }
    setEditingService(full);
    setIsDrawerOpen(true);
  };

  const handleDrawerSuccess = () => {
    setIsDrawerOpen(false);
    const main = document.querySelector('main');
    if (main) main.scrollTo({ top: 0, behavior: 'smooth' });
    fetchServices();
  };

  const handleToggleClick = (svc: Service) => {
    setPendingAction({ type: "toggle", service: svc });
    setConfirmOpen(true);
  };

  const handleDeleteClick = (svc: Service) => {
    setPendingAction({ type: "delete", service: svc });
    setConfirmOpen(true);
  };

  const handleConfirmAction = async () => {
    if (!pendingAction) return;
    
    setActionLoading(true);
    try {
      if (pendingAction.type === "delete") {
        await client(`/api/services/${pendingAction.service.id}`, {
          method: "DELETE",
        });
      } else if (pendingAction.type === "toggle") {
        // Toggling is a full-object upsert to POST /services, which validates against
        // ServiceCreate (entry_command required). The list projection (ServiceListItem)
        // omits the command fields, so fetch the full service first; a failed fetch aborts
        // via the catch below rather than POSTing an incomplete body.
        const full = await client(`/api/services/${pendingAction.service.id}`);
        const updatedService = {
            ...full,
            is_active: !pendingAction.service.is_active,
        };
        await client("/api/services", {
          method: "POST",
          json: updatedService,
        });
      }
      fetchServices(true); 
      setConfirmOpen(false);
      setPendingAction(null);
    } catch (e) {
      console.error(`Failed to ${pendingAction.type} service`, e);
      setErrorMessage(t("common.operationFailed"));
    } finally {
      setActionLoading(false);
    }
  };

  const getDialogConfig = () => {
    if (!pendingAction) return { title: "", description: "", variant: "default" as const, confirmText: "" };

    if (pendingAction.type === "delete") {
      return {
        title: t("services.deleteTitle"),
        description: (
          <span>
            {t("services.deleteConfirm", { name: pendingAction.service.name })}
            {" "}{t("services.deleteWarning")}
          </span>
        ),
        variant: "danger" as const,
        confirmText: t("services.deleteTitle"),
        confirmInput: pendingAction.service.id,
      };
    } else {
      const isStopping = pendingAction.service.is_active;
      return {
        title: isStopping ? t("services.stopTitle") : t("services.startTitle"),
        description: isStopping ? (
          <span>
            {t("services.stopConfirm", { name: pendingAction.service.name })}
            {" "}{t("services.stopWarning")}
          </span>
        ) : (
          <span>
            {t("services.startConfirm", { name: pendingAction.service.name })}
            {" "}{t("services.startWarning")}
          </span>
        ),
        variant: isStopping ? "danger" as const : "default" as const,
        confirmText: isStopping ? t("services.stopTitle") : t("services.startTitle"),
      };
    }
  };

  const dialogConfig = getDialogConfig();

  return (
    <div className="relative min-h-[calc(100vh-8rem)] pb-20">
      <style jsx global>{`
        ::-webkit-scrollbar {
          display: none;
        }
        html {
          -ms-overflow-style: none;
          scrollbar-width: none;
        }
      `}</style>

      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center sm:justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            {t("nav.services")}
          </h1>
          <p className="text-zinc-500 text-sm mt-1">
            {t("services.subtitle")}
          </p>
        </div>
        <button
          onClick={handleCreate}
          className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors shadow-lg shadow-blue-900/20 active:scale-95 border border-blue-500/50"
        >
          <Plus className="w-4 h-4" /> {t("services.new")}
        </button>
      </div>

      {/* Filters & Search & Sort */}
      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-1.5 mb-6 flex flex-wrap items-center gap-2 backdrop-blur-sm relative z-20">

        {/* 1. Search (Expanded) */}
        <div className="relative flex-1 group">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 group-focus-within:text-blue-500 transition-colors" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t("services.searchPlaceholder")}
            className="w-full bg-transparent border-none py-2.5 pl-9 pr-4 text-sm text-zinc-200 focus:outline-none focus:ring-0 placeholder-zinc-600"
          />
        </div>

        <div className="h-6 w-px bg-zinc-800 hidden sm:block"></div>

        {/* 2. User Filter */}
        <div className="w-full sm:w-48">
          <SearchableSelect
            value={selectedUserId}
            onChange={(uid) => setParams({ owner_id: uid || null, page: null })}
            options={userFilterOptions}
            placeholder={t("services.filterByOwner")}
            className="mb-0 border-none bg-transparent"
          />
        </div>

        <div className="h-6 w-px bg-zinc-800 hidden sm:block"></div>

        {/* 3. Sort Order [Magnus Update] */}
        <div className="w-44">
           <SearchableSelect
             value={sortBy}
             onChange={setSortBy}
             options={SORT_OPTIONS}
             className="mb-0 border-none bg-transparent"
             // 这里的 icon 是字符串url，我们这里没有图标，所以仅依赖文字
           />
        </div>

        <div className="h-6 w-px bg-zinc-800 hidden sm:block"></div>

        {/* 4. Active Only Toggle [Magnus Update] */}
        <button
          onClick={() => setActiveOnly(!activeOnly)}
          className={`px-3 py-2.5 text-sm font-medium flex items-center gap-2 transition-colors rounded-lg mr-1
            ${activeOnly
              ? "text-teal-400 bg-teal-900/20 hover:bg-teal-900/30"
              : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50"
            }`}
          title={t("services.activeOnlyTitle")}
        >
          <Activity className={`w-4 h-4 ${activeOnly ? "animate-pulse" : ""}`} />
          <span className="whitespace-nowrap">{t("services.activeOnly")}</span>
        </button>

      </div>

      {/* Table Content */}
      <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl overflow-hidden backdrop-blur-sm">
        <ServiceTable
          services={services}
          loading={loading}
          onClone={handleClone}
          onToggle={handleToggleClick}
          onDelete={handleDeleteClick}
          onRefresh={() => fetchServices(true)}
        />
        {services.length > 0 && (
          <div className="px-6 py-2 border-zinc-900/30">
            <PaginationControls
              currentPage={page}
              totalPages={Math.ceil(totalItems / pageSize)}
              pageSize={pageSize}
              totalItems={totalItems}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
            />
          </div>
        )}
      </div>

      <ServiceDrawer
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        initialData={editingService}
        onSuccess={handleDrawerSuccess}
      />

      <ConfirmationDialog
        isOpen={confirmOpen}
        onClose={() => !actionLoading && setConfirmOpen(false)}
        onConfirm={handleConfirmAction}
        title={dialogConfig.title}
        description={dialogConfig.description}
        confirmText={dialogConfig.confirmText}
        variant={dialogConfig.variant}
        isLoading={actionLoading}
        confirmInput={dialogConfig.confirmInput}
      />

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

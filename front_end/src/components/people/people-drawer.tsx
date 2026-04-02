// front_end/src/components/people/people-drawer.tsx
"use client";

import { useState, useEffect, useRef } from "react";
import { Users, Shield, Eye, EyeOff, PenLine, RefreshCw, UserMinus, Camera, Check, X } from "lucide-react";
import { client } from "@/lib/api";
import { Drawer } from "@/components/ui/drawer";
import { CopyableText } from "@/components/ui/copyable-text";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { TokenInput, MAGNUS_TOKEN_LENGTH, validateToken } from "@/components/ui/token-input";
import { AvatarCircle } from "@/components/ui/user-avatar";
import { useLanguage } from "@/context/language-context";
import { useAuth } from "@/context/auth-context";
import { formatBeijingTime } from "@/lib/utils";
import { UserDetail } from "@/types/auth";


interface PeopleDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  user: UserDetail | null;
  onRefresh: () => void;
}


export function PeopleDrawer({ isOpen, onClose, user, onRefresh }: PeopleDrawerProps) {
  const { t } = useLanguage();
  const { user: currentUser } = useAuth();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Token state
  const [drawerToken, setDrawerToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [isRefreshingToken, setIsRefreshingToken] = useState(false);
  const [showResetDialog, setShowResetDialog] = useState(false);

  // Custom token state
  const [showCustomInput, setShowCustomInput] = useState(false);
  const [customToken, setCustomToken] = useState("");
  const [customTokenError, setCustomTokenError] = useState<string | null>(null);
  const [isSavingCustom, setIsSavingCustom] = useState(false);

  // Delete state
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  // Headcount editing
  const [editingHeadcount, setEditingHeadcount] = useState(false);
  const [headcountValue, setHeadcountValue] = useState("");
  const [headcountError, setHeadcountError] = useState(false);

  // Avatar
  const [avatarKey, setAvatarKey] = useState(0);
  const [isUploadingAvatar, setIsUploadingAvatar] = useState(false);

  // Error
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Reset all state when user changes
  useEffect(() => {
    if (!user) return;
    setShowToken(false);
    setDrawerToken("");
    setEditingHeadcount(false);
    setHeadcountValue(String(user.headcount ?? 0));
    setShowResetDialog(false);
    setShowDeleteConfirm(false);
    setShowCustomInput(false);
    setCustomToken("");
    setCustomTokenError(null);

    // Fetch token only if current user has permission (self / parent / admin)
    const hasTokenAccess = currentUser && (
      user.id === currentUser.id || currentUser.is_admin || user.parent_id === currentUser.id
    );
    if (hasTokenAccess) {
      client(`/api/users/${user.id}/token`)
        .then((res) => setDrawerToken(res.magnus_token || ""))
        .catch(() => {});
    }
  }, [user?.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  const maskedToken = "sk-" + "\u2022".repeat(MAGNUS_TOKEN_LENGTH - 3);
  const displayToken = showToken && drawerToken ? drawerToken : maskedToken;

  const canEditHeadcount = user && currentUser && user.user_type === "agent" && (
    currentUser.is_admin || user.parent_id === currentUser.id
  );

  const canAccessToken = user && currentUser && (
    user.id === currentUser.id || currentUser.is_admin || user.parent_id === currentUser.id
  );

  // --- Handlers ---

  const handleRefreshToken = async () => {
    if (!user) return;
    setIsRefreshingToken(true);
    try {
      const res = await client(`/api/users/${user.id}/token/refresh`, { method: "POST" });
      setDrawerToken(res.magnus_token);
      setShowToken(false);
      closeResetDialog();
    } catch (e: any) {
      console.error(e);
      setErrorMessage(`${t("header.refreshFailed")} ${e.message || "Unknown error"}`);
    } finally {
      setIsRefreshingToken(false);
    }
  };

  const handleSaveCustomToken = async () => {
    if (!user) return;
    const trimmed = customToken.trim();
    if (!validateToken(trimmed)) {
      setCustomTokenError(t("header.customTokenInvalid"));
      return;
    }
    setIsSavingCustom(true);
    setCustomTokenError(null);
    try {
      const res = await client(`/api/users/${user.id}/token/set`, {
        method: "POST",
        body: JSON.stringify({ token: trimmed }),
      });
      setDrawerToken(res.magnus_token);
      setShowToken(false);
      closeResetDialog();
    } catch (e: any) {
      setCustomTokenError(e.message || "Failed to set token");
    } finally {
      setIsSavingCustom(false);
    }
  };

  const closeResetDialog = () => {
    setShowResetDialog(false);
    setShowCustomInput(false);
    setCustomToken("");
    setCustomTokenError(null);
  };

  const handleDelete = async () => {
    if (!user) return;
    setIsDeleting(true);
    try {
      await client(`/api/users/${user.id}`, { method: "DELETE" });
      onClose();
      onRefresh();
    } catch (e) {
      console.error(e);
    } finally {
      setIsDeleting(false);
      setShowDeleteConfirm(false);
    }
  };

  const handleHeadcountSave = async () => {
    if (!user) return;
    const parsed = parseInt(headcountValue, 10);
    if (isNaN(parsed) || parsed < 0) {
      setHeadcountError(true);
      return;
    }
    setHeadcountError(false);
    try {
      await client(`/api/users/${user.id}/headcount`, {
        method: "PATCH",
        json: { headcount: parsed },
      });
      onRefresh();
      setEditingHeadcount(false);
    } catch (e: any) {
      const detail = e?.message || "Failed to update headcount";
      setErrorMessage(detail);
      setEditingHeadcount(false);
    }
  };

  const handleAvatarUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !user) return;
    setIsUploadingAvatar(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const token = localStorage.getItem("magnus_token");
      const res = await fetch(`/api/users/${user.id}/avatar`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      if (!res.ok) throw new Error("Upload failed");
      setAvatarKey((k) => k + 1);
      onRefresh();
    } catch (err) {
      console.error(err);
    } finally {
      setIsUploadingAvatar(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <>
      <Drawer
        isOpen={isOpen}
        onClose={onClose}
        title={t("people.drawer.title")}
        icon={<Users className="w-5 h-5 text-blue-500" />}
        width="w-[400px]"
      >
        {user && (
          <div className="flex flex-col min-h-full">
            <div className="flex-1 space-y-4">

              {/* Avatar + Name */}
              <div className="flex items-center gap-4">
                <div
                  className="relative group cursor-pointer"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <div key={avatarKey}>
                    <AvatarCircle user={user} size="lg" />
                  </div>
                  <div className="absolute inset-0 rounded-full bg-black/50 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                    {isUploadingAvatar ? (
                      <RefreshCw className="w-5 h-5 text-white animate-spin" />
                    ) : (
                      <Camera className="w-5 h-5 text-white" />
                    )}
                  </div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    className="hidden"
                    onChange={handleAvatarUpload}
                  />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-lg font-semibold text-zinc-100">{user.name}</h3>
                    {user.is_admin && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-900/30 text-amber-400 border border-amber-800/50">
                        <Shield className="w-2.5 h-2.5" />
                        {t("people.role.admin")}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Token — only visible to self / parent / admin */}
              {canAccessToken && (
              <div>
                <div className="flex items-center gap-1 bg-zinc-900/50 rounded-lg border border-zinc-800/50 px-2 py-1.5">
                  <div className="flex-1 min-w-0 whitespace-nowrap">
                    <CopyableText
                      text={displayToken}
                      copyValue={drawerToken}
                      variant="id"
                      className="!text-zinc-400 hover:!text-blue-400 [&>span]:!whitespace-nowrap [&>span]:!overflow-visible"
                    />
                  </div>
                  <div className="w-px h-3.5 bg-zinc-800 mx-1" />
                  <button
                    onClick={() => setShowToken(!showToken)}
                    className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-all"
                  >
                    {showToken ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                  </button>
                  <button
                    onClick={() => setShowResetDialog(true)}
                    className="p-1.5 text-zinc-500 hover:text-blue-400 hover:bg-blue-500/10 rounded-md transition-all"
                  >
                    <PenLine className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              )}

              {/* Fields — horizontal layout */}
              <div className="space-y-2.5">
                {/* +1 Leader */}
                <div className="flex items-center gap-5">
                  <span className="text-sm font-medium text-zinc-300 shrink-0">{t("people.table.leader")}</span>
                  {user.parent_name ? (
                    <div className="flex items-center gap-1.5">
                      <AvatarCircle user={{ name: user.parent_name, avatar_url: user.parent_avatar_url ?? null }} size="xs" />
                      <span className="text-sm text-zinc-400">{user.parent_name}</span>
                    </div>
                  ) : (
                    <span className="text-sm text-zinc-600 italic">{t("people.leader.void")}</span>
                  )}
                </div>

                {/* Headcount */}
                <div className="flex items-center gap-5">
                  <span className="text-sm font-medium text-zinc-300 shrink-0">{t("people.drawer.headcount")}</span>
                  {editingHeadcount ? (
                    <div className="flex items-center gap-1.5">
                      <div className={`flex items-center gap-1.5 bg-zinc-900/80 border rounded-lg px-2 py-1 ${headcountError ? "border-red-500/70" : "border-zinc-700"}`}>
                        <input
                          type="text"
                          inputMode="numeric"
                          value={headcountValue}
                          onChange={(e) => { setHeadcountValue(e.target.value); setHeadcountError(false); }}
                          onKeyDown={(e) => { if (e.key === "Enter") handleHeadcountSave(); if (e.key === "Escape") setEditingHeadcount(false); }}
                          autoFocus
                          className="w-16 px-1 bg-transparent text-sm text-zinc-200 font-mono focus:outline-none"
                        />
                        <button onClick={handleHeadcountSave} className="p-0.5 text-green-400 hover:text-green-300"><Check className="w-3.5 h-3.5" /></button>
                        <button onClick={() => setEditingHeadcount(false)} className="p-0.5 text-zinc-500 hover:text-zinc-300"><X className="w-3.5 h-3.5" /></button>
                      </div>
                    </div>
                  ) : (
                    <span className="text-sm font-mono">
                      <span className="text-zinc-400">{user.available_headcount == null ? "\u221E" : user.available_headcount}</span>
                      <span className="text-zinc-600 mx-1">/</span>
                      <span
                        className={`text-zinc-500 ${canEditHeadcount ? "cursor-pointer hover:text-blue-400 transition-colors border-b border-dashed border-zinc-700 hover:border-blue-400" : ""}`}
                        onClick={() => {
                          if (!canEditHeadcount) return;
                          setHeadcountValue(String(user.headcount ?? 0));
                          setEditingHeadcount(true);
                        }}
                        title={canEditHeadcount ? t("people.drawer.editHeadcount") : undefined}
                      >
                        {user.headcount == null ? "\u221E" : user.headcount}
                      </span>
                    </span>
                  )}
                </div>

                {/* BP / Svc */}
                <div className="flex items-baseline gap-5">
                  <span className="text-sm font-medium text-zinc-300 shrink-0">{t("people.table.bpSvc")}</span>
                  <span className="text-sm text-zinc-400 font-mono">{user.blueprint_count} / {user.service_count} / {user.skill_count}</span>
                </div>

                {/* Joined */}
                <div className="flex items-baseline gap-5">
                  <span className="text-sm font-medium text-zinc-300 shrink-0">{t("people.drawer.created")}</span>
                  <span className="text-sm text-zinc-400 font-mono">{formatBeijingTime(user.created_at)}</span>
                </div>
              </div>
            </div>

            {/* Footer: Offboard (managed agent only) */}
            {user.user_type === "agent" && (
              <div className="mt-auto pt-6 border-t border-zinc-800 flex justify-end pb-1">
                <button
                  onClick={() => setShowDeleteConfirm(true)}
                  className="px-4 py-2 rounded-lg text-sm font-medium text-red-400 hover:text-white hover:bg-red-600/80 border border-red-800/50 transition-colors flex items-center gap-2"
                >
                  <UserMinus className="w-4 h-4" />
                  {t("people.drawer.offboard")}
                </button>
              </div>
            )}
          </div>
        )}
      </Drawer>

      {/* Token Reset Dialog — replicated from header with hidden "Edit Token" */}
      {showResetDialog && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 min-h-screen">
          <div
            className="fixed inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
            onClick={() => !isRefreshingToken && !isSavingCustom && closeResetDialog()}
          />
          <div className="relative bg-[#09090b] border border-zinc-800 rounded-xl shadow-2xl w-full max-w-md overflow-hidden">
            <div className="p-6">
              <div className="flex items-start gap-4">
                <div className="p-3 rounded-full flex-shrink-0 bg-red-500/10 text-red-500">
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                  </svg>
                </div>
                <div className="flex-1">
                  <h3 className="text-lg font-semibold text-zinc-100 leading-none mb-2">
                    {t("header.resetTokenTitle")}
                  </h3>
                  <div className="text-sm text-zinc-400 leading-relaxed">
                    {t("header.resetTokenDesc")} <br/><br/>
                    <span className="text-red-400">{t("header.resetTokenWarning")}</span>
                    {" "}{t("header.resetTokenNote")}
                  </div>
                </div>
              </div>

              {/* Custom token input — revealed on click */}
              {showCustomInput && (
                <TokenInput
                  value={customToken}
                  onChange={setCustomToken}
                  error={customTokenError}
                  onClearError={() => setCustomTokenError(null)}
                  label={t("header.customTokenLabel")}
                  warning={t("header.customTokenWarning")}
                  onSubmit={handleSaveCustomToken}
                />
              )}
            </div>

            <div className="bg-zinc-900/50 px-6 py-4 flex items-center justify-between border-t border-zinc-800/50">
              {/* Hidden "Edit Token" button — only visible on hover */}
              <button
                onClick={() => setShowCustomInput(!showCustomInput)}
                className="text-xs text-transparent hover:text-zinc-500 transition-colors cursor-pointer"
              >
                {showCustomInput ? t("common.cancel") : t("header.editToken")}
              </button>

              <div className="flex items-center gap-3">
                <button
                  onClick={closeResetDialog}
                  disabled={isRefreshingToken || isSavingCustom}
                  className="px-4 py-2 rounded-lg text-sm font-medium text-zinc-300 hover:text-white hover:bg-zinc-800 transition-colors disabled:opacity-50"
                >
                  {t("common.cancel")}
                </button>
                {showCustomInput ? (
                  <button
                    onClick={handleSaveCustomToken}
                    disabled={isSavingCustom || customToken.length !== MAGNUS_TOKEN_LENGTH}
                    className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 border border-blue-500/50 shadow-lg shadow-blue-900/20 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isSavingCustom && <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>}
                    {t("header.saveCustomToken")}
                  </button>
                ) : (
                  <button
                    onClick={handleRefreshToken}
                    disabled={isRefreshingToken}
                    className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-red-600 hover:bg-red-500 border border-red-500/50 shadow-lg shadow-red-900/20 transition-all flex items-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed"
                  >
                    {isRefreshingToken && <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>}
                    {t("header.resetToken")}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Offboard Confirmation */}
      <ConfirmationDialog
        isOpen={showDeleteConfirm}
        onClose={() => setShowDeleteConfirm(false)}
        onConfirm={handleDelete}
        title={t("people.drawer.offboard")}
        description={t("people.drawer.offboardConfirm", { name: user?.name || "" })}
        confirmText={t("people.drawer.offboard")}
        isLoading={isDeleting}
        variant="danger"
        confirmInput={user?.name}
      />

      {/* Error alert */}
      <ConfirmationDialog
        isOpen={!!errorMessage}
        onClose={() => setErrorMessage(null)}
        title={t("common.error")}
        description={errorMessage}
        confirmText={t("common.ok")}
        mode="alert"
        variant="danger"
      />
    </>
  );
}

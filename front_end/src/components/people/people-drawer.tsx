// front_end/src/components/people/people-drawer.tsx
"use client";

import { useState, useEffect, useRef } from "react";
import { Users, Shield, Eye, EyeOff, PenLine, RefreshCw, Trash2, Camera, Check, X, Bot } from "lucide-react";
import { client } from "@/lib/api";
import { Drawer } from "@/components/ui/drawer";
import { CopyableText } from "@/components/ui/copyable-text";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { Avatar } from "./people-table";
import { useLanguage } from "@/context/language-context";
import { useAuth } from "@/context/auth-context";
import { formatBeijingTime } from "@/lib/utils";
import { UserDetail } from "@/types/auth";

const MAGNUS_TOKEN_LENGTH = 35;

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

  // Bot credentials
  const [botAppId, setBotAppId] = useState("");
  const [botAppSecret, setBotAppSecret] = useState("");
  const [showAppSecret, setShowAppSecret] = useState(false);
  const [showRefreshCredsDialog, setShowRefreshCredsDialog] = useState(false);
  const [isRefreshingCreds, setIsRefreshingCreds] = useState(false);

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

    // Fetch bot credentials for agent users
    setBotAppId("");
    setBotAppSecret("");
    setShowAppSecret(false);
    setShowRefreshCredsDialog(false);
    if (user.user_type === "agent" && hasTokenAccess) {
      client(`/api/users/${user.id}/bot-credentials`)
        .then((res) => { setBotAppId(res.app_id || ""); setBotAppSecret(res.app_secret || ""); })
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
    if (!trimmed.startsWith("sk-") || trimmed.length !== MAGNUS_TOKEN_LENGTH) {
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

  const handleRefreshBotCredentials = async () => {
    if (!user) return;
    setIsRefreshingCreds(true);
    try {
      const res = await client(`/api/users/${user.id}/bot-credentials/refresh`, { method: "POST" });
      setBotAppId(res.app_id);
      setBotAppSecret(res.app_secret);
      setShowAppSecret(false);
      setShowRefreshCredsDialog(false);
    } catch (e: any) {
      setErrorMessage(e.message || "Failed to refresh credentials");
    } finally {
      setIsRefreshingCreds(false);
    }
  };

  const handleAvatarUpload = async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
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
                    <Avatar user={user} size="lg" />
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

              {/* Bot Credentials — only for agent users */}
              {user.user_type === "agent" && canAccessToken && botAppId && (
              <div>
                <div className="flex items-center gap-2 mb-1.5">
                  <Bot className="w-3.5 h-3.5 text-zinc-500" />
                  <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider">{t("people.drawer.botCredentials")}</span>
                </div>
                <div className="space-y-1.5">
                  {/* App ID */}
                  <div className="flex items-center gap-1 bg-zinc-900/50 rounded-lg border border-zinc-800/50 px-2 py-1.5">
                    <span className="text-[10px] text-zinc-600 font-medium shrink-0 w-14">{t("people.drawer.appId")}</span>
                    <div className="flex-1 min-w-0">
                      <CopyableText text={botAppId} variant="id" className="!text-zinc-400" />
                    </div>
                  </div>
                  {/* App Secret */}
                  <div className="flex items-center gap-1 bg-zinc-900/50 rounded-lg border border-zinc-800/50 px-2 py-1.5">
                    <span className="text-[10px] text-zinc-600 font-medium shrink-0 w-14">{t("people.drawer.appSecret")}</span>
                    <div className="flex-1 min-w-0">
                      <CopyableText
                        text={showAppSecret ? botAppSecret : "\u2022".repeat(24)}
                        copyValue={botAppSecret}
                        variant="id"
                        className="!text-zinc-400"
                      />
                    </div>
                    <div className="w-px h-3.5 bg-zinc-800 mx-1" />
                    <button
                      onClick={() => setShowAppSecret(!showAppSecret)}
                      className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-all"
                    >
                      {showAppSecret ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                    <button
                      onClick={() => setShowRefreshCredsDialog(true)}
                      className="p-1.5 text-zinc-500 hover:text-blue-400 hover:bg-blue-500/10 rounded-md transition-all"
                      title={t("people.drawer.refreshCredentials")}
                    >
                      <RefreshCw className="w-3.5 h-3.5" />
                    </button>
                  </div>
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
                      <Avatar user={{ name: user.parent_name, avatar_url: user.parent_avatar_url ?? null, user_type: "human" }} size="xs" />
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
                  <span className="text-sm text-zinc-400 font-mono">{user.blueprint_count} / {user.service_count}</span>
                </div>

                {/* Joined */}
                <div className="flex items-baseline gap-5">
                  <span className="text-sm font-medium text-zinc-300 shrink-0">{t("people.drawer.created")}</span>
                  <span className="text-sm text-zinc-400 font-mono">{formatBeijingTime(user.created_at)}</span>
                </div>
              </div>
            </div>

            {/* Footer: Delete (managed member only) */}
            {user.user_type === "agent" && (
              <div className="mt-auto pt-6 border-t border-zinc-800 flex justify-end pb-1">
                <button
                  onClick={() => setShowDeleteConfirm(true)}
                  className="px-4 py-2 rounded-lg text-sm font-medium text-red-400 hover:text-white hover:bg-red-600/80 border border-red-800/50 transition-colors flex items-center gap-2"
                >
                  <Trash2 className="w-4 h-4" />
                  {t("people.drawer.delete")}
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
                <div className="mt-4 pt-4 border-t border-zinc-800/50">
                  <label className="text-xs text-zinc-500 font-medium block mb-1">{t("header.customTokenLabel")}</label>
                  <p className="text-xs text-red-400/80 mb-2">{t("header.customTokenWarning")}</p>
                  <input
                    type="text"
                    value={customToken}
                    onChange={(e) => { setCustomToken(e.target.value); setCustomTokenError(null); }}
                    placeholder="sk-..."
                    maxLength={MAGNUS_TOKEN_LENGTH}
                    className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm font-mono text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
                    autoFocus
                    onKeyDown={(e) => { if (e.key === "Enter") handleSaveCustomToken(); }}
                  />
                  <div className="flex items-center justify-between mt-2">
                    <span className={`text-xs ${customToken.length === MAGNUS_TOKEN_LENGTH ? "text-green-500" : "text-zinc-600"}`}>
                      {customToken.length}/{MAGNUS_TOKEN_LENGTH}
                    </span>
                    {customTokenError && <span className="text-xs text-red-400">{customTokenError}</span>}
                  </div>
                  <button
                    onClick={handleSaveCustomToken}
                    disabled={isSavingCustom || customToken.length !== MAGNUS_TOKEN_LENGTH}
                    className="mt-2 w-full px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 border border-blue-500/50 shadow-lg transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isSavingCustom ? "..." : t("header.saveCustomToken")}
                  </button>
                </div>
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
                  disabled={isRefreshingToken}
                  className="px-4 py-2 rounded-lg text-sm font-medium text-zinc-300 hover:text-white hover:bg-zinc-800 transition-colors disabled:opacity-50"
                >
                  {t("common.cancel")}
                </button>
                <button
                  onClick={handleRefreshToken}
                  disabled={isRefreshingToken}
                  className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-red-600 hover:bg-red-500 border border-red-500/50 shadow-lg shadow-red-900/20 transition-all flex items-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed"
                >
                  {isRefreshingToken && <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>}
                  {t("header.resetToken")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation */}
      <ConfirmationDialog
        isOpen={showDeleteConfirm}
        onClose={() => setShowDeleteConfirm(false)}
        onConfirm={handleDelete}
        title={t("people.drawer.delete")}
        description={t("people.drawer.deleteConfirm")}
        confirmText={t("people.drawer.delete")}
        isLoading={isDeleting}
        variant="danger"
      />

      {/* Refresh Bot Credentials Confirmation */}
      <ConfirmationDialog
        isOpen={showRefreshCredsDialog}
        onClose={() => setShowRefreshCredsDialog(false)}
        onConfirm={handleRefreshBotCredentials}
        title={t("people.drawer.refreshCredentialsTitle")}
        description={t("people.drawer.refreshCredentialsDesc")}
        confirmText={t("people.drawer.refreshCredentials")}
        isLoading={isRefreshingCreds}
        variant="danger"
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

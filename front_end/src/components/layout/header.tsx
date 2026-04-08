// front_end/src/components/layout/header.tsx
"use client";

import { useState, useRef, useEffect } from "react";
import { Eye, EyeOff, PenLine } from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { AvatarCircle } from "@/components/ui/user-avatar";
import { client } from "@/lib/api";
import { NotificationsPopover } from "./notifications-popover";
import { MobileNav } from "./mobile-nav";
import { LanguageToggle } from "./language-toggle";
import { CopyableText } from "@/components/ui/copyable-text";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { TokenInput, MAGNUS_TOKEN_LENGTH, validateToken } from "@/components/ui/token-input";
import { CLUSTER_CONFIG } from "@/lib/config";

export function Header() {
  const { user, isLoading } = useAuth();
  const { t } = useLanguage();

  const [isOpen, setIsOpen] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [magnusToken, setMagnusToken] = useState<string | null>(null);

  const [showResetDialog, setShowResetDialog] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Custom token edit state
  const [showCustomInput, setShowCustomInput] = useState(false);
  const [customToken, setCustomToken] = useState("");
  const [customTokenError, setCustomTokenError] = useState<string | null>(null);
  const [isSavingCustom, setIsSavingCustom] = useState(false);

  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setShowToken(false);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  // 打开 popover 时按需拉取 MAGNUS_TOKEN
  useEffect(() => {
    if (isOpen && magnusToken === null) {
      client("/api/auth/my-token")
        .then((resp) => setMagnusToken(resp.magnus_token || ""))
        .catch(() => setMagnusToken(""));
    }
  }, [isOpen, magnusToken]);

  const handleRefreshToken = async () => {
    setIsRefreshing(true);
    try {
      const resp = await client("/api/auth/token/refresh", { method: "POST" });
      setMagnusToken(resp.magnus_token);

      setShowResetDialog(false);
      setShowToken(false);
      setShowCustomInput(false);
      setCustomToken("");
      setCustomTokenError(null);
    } catch (error: any) {
      console.error("Refresh failed:", error);
      setErrorMessage(`${t("header.refreshFailed")} ${error.message || "Unknown error"}`);
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleSaveCustomToken = async () => {
    const trimmed = customToken.trim();
    if (!validateToken(trimmed)) {
      setCustomTokenError(t("header.customTokenInvalid"));
      return;
    }
    setIsSavingCustom(true);
    setCustomTokenError(null);
    try {
      const resp = await client("/api/auth/token/set", {
        method: "POST",
        body: JSON.stringify({ token: trimmed }),
      });
      setMagnusToken(resp.magnus_token);
      setShowResetDialog(false);
      setShowToken(false);
      setShowCustomInput(false);
      setCustomToken("");
    } catch (error: any) {
      setCustomTokenError(error.message || "Failed to set token");
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

  // sk- (3) + token_urlsafe(24) (32) = 35 字符
  const realToken = magnusToken || "";
  const maskedToken = "sk-" + "•".repeat(MAGNUS_TOKEN_LENGTH - 3);
  const displayToken = showToken && realToken ? realToken : maskedToken;

  return (
    <>
      <header className="h-16 border-b border-zinc-800 bg-zinc-950/50 backdrop-blur sticky top-0 z-40 flex items-center justify-between px-4 md:px-8 gap-4">
        <div className="flex items-center">
          <MobileNav />
        </div>
        <div className="flex items-center gap-4">
        <NotificationsPopover />
        <LanguageToggle />

        {!isLoading && user && (
          <div className="relative" ref={popoverRef}>
            <button 
              onClick={() => setIsOpen(!isOpen)}
              className={`flex items-center gap-3 pl-4 border-l border-zinc-800 transition-colors group outline-none
                ${isOpen ? "opacity-100" : "opacity-90 hover:opacity-100"}`}
            >
              <div className="text-right hidden md:block">
                <div className="text-sm font-medium text-zinc-200 leading-none mb-1 group-hover:text-blue-400 transition-colors">
                  {user.name}
                </div>
                <div className="text-xs text-zinc-500 font-mono">
                  {user.email || CLUSTER_CONFIG.name}
                </div>
              </div>
              
              <div className={`rounded-full transition-all ${isOpen ? "ring-2 ring-blue-500/20" : ""}`}>
                <AvatarCircle user={user} size="sm" />
              </div>
            </button>

            {isOpen && (
              <div className="absolute top-full right-0 mt-3 w-fit max-w-[calc(100vw-2rem)] bg-zinc-950 border border-zinc-800 rounded-xl shadow-2xl ring-1 ring-white/5 p-1.5 animate-in fade-in slide-in-from-top-2 duration-200">
                <div className="flex items-center gap-1 bg-zinc-900/50 rounded-lg border border-zinc-800/50 px-2 py-1.5">
                  <div className="break-all">
                    <CopyableText
                      text={displayToken}
                      copyValue={realToken}
                      variant="id"
                      className="!text-zinc-400 hover:!text-blue-400"
                    />
                  </div>
                  
                  <div className="w-px h-3.5 bg-zinc-800 mx-1"></div>

                  <button
                    onClick={(e) => { e.stopPropagation(); setShowToken(!showToken); }}
                    className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-all"
                    title={showToken ? t("header.hideToken") : t("header.showToken")}
                  >
                    {showToken ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                  </button>

                  <button
                    onClick={(e) => { e.stopPropagation(); setShowResetDialog(true); }}
                    className="p-1.5 text-zinc-500 hover:text-blue-400 hover:bg-blue-500/10 rounded-md transition-all"
                    title={t("header.resetToken")}
                  >
                    <PenLine className="w-3.5 h-3.5" />
                  </button>
                </div>
                
                <div className="absolute -top-1.5 right-3 w-3 h-3 bg-zinc-950 border-t border-l border-zinc-800 rotate-45"></div>
              </div>
            )}
          </div>
        )}
        </div>
      </header>

      {/* Reset Token Dialog — custom layout with hidden "Edit Token" button */}
      {showResetDialog && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 min-h-screen">
          <div
            className="fixed inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
            onClick={() => !isRefreshing && !isSavingCustom && closeResetDialog()}
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
                  disabled={isRefreshing || isSavingCustom}
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
                    disabled={isRefreshing}
                    className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-red-600 hover:bg-red-500 border border-red-500/50 shadow-lg shadow-red-900/20 transition-all flex items-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed"
                  >
                    {isRefreshing && <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>}
                    {t("header.resetToken")}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

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
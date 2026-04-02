// front_end/src/components/auth/login-dialog.tsx
"use client";

import { useState } from "react";
import { LogIn } from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { TokenInput, MAGNUS_TOKEN_LENGTH, validateToken } from "@/components/ui/token-input";

export function LoginDialog() {
  const { showLoginDialog, setShowLoginDialog, loginWithFeishu, loginWithToken } = useAuth();
  const { t } = useLanguage();

  const [showTokenInput, setShowTokenInput] = useState(false);
  const [tokenValue, setTokenValue] = useState("");
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  const handleTokenLogin = async () => {
    const trimmed = tokenValue.trim();
    if (!validateToken(trimmed)) {
      setTokenError(t("header.customTokenInvalid"));
      return;
    }
    setIsLoggingIn(true);
    setTokenError(null);
    const err = await loginWithToken(trimmed);
    if (err) {
      setTokenError(t("auth.tokenLoginError"));
    }
    setIsLoggingIn(false);
  };

  const closeDialog = () => {
    if (isLoggingIn) return;
    setShowLoginDialog(false);
    setShowTokenInput(false);
    setTokenValue("");
    setTokenError(null);
  };

  if (!showLoginDialog) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 min-h-screen">
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
        onClick={closeDialog}
      />
      <div className="relative bg-[#09090b] border border-zinc-800 rounded-xl shadow-2xl w-full max-w-md overflow-hidden">
        <div className="p-6">
          <div className="flex items-start gap-4">
            <div className="p-3 rounded-full flex-shrink-0 bg-blue-500/10 text-blue-500">
              <LogIn className="w-6 h-6" />
            </div>
            <div className="flex-1">
              <h3 className="text-lg font-semibold text-zinc-100 leading-none mb-2">
                {t("auth.required")}
              </h3>
              <div className="text-sm text-zinc-400 leading-relaxed">
                {t("auth.requiredDesc")}
              </div>
            </div>
          </div>

          {showTokenInput && (
            <TokenInput
              value={tokenValue}
              onChange={setTokenValue}
              error={tokenError}
              onClearError={() => setTokenError(null)}
              placeholder={t("auth.tokenPlaceholder")}
              label={t("auth.tokenLogin")}
              onSubmit={handleTokenLogin}
            />
          )}
        </div>

        <div className="bg-zinc-900/50 px-6 py-4 flex items-center justify-between border-t border-zinc-800/50">
          <button
            onClick={() => setShowTokenInput(!showTokenInput)}
            className="text-xs text-transparent hover:text-zinc-500 transition-colors cursor-pointer"
          >
            {showTokenInput ? t("common.cancel") : t("auth.tokenLogin")}
          </button>

          <div className="flex items-center gap-3">
            <button
              onClick={closeDialog}
              className="px-4 py-2 rounded-lg text-sm font-medium text-zinc-300 hover:text-white hover:bg-zinc-800 transition-colors"
            >
              {t("common.cancel")}
            </button>
            {showTokenInput ? (
              <button
                onClick={handleTokenLogin}
                disabled={isLoggingIn || tokenValue.length !== MAGNUS_TOKEN_LENGTH}
                className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 border border-blue-500/50 shadow-lg shadow-blue-900/20 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isLoggingIn && <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>}
                {t("auth.tokenLoginButton")}
              </button>
            ) : (
              <button
                onClick={loginWithFeishu}
                className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 border border-blue-500/50 shadow-lg shadow-blue-900/20 transition-all flex items-center gap-2"
              >
                <LogIn className="w-4 h-4" />
                {t("auth.signInWithFeishu")}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

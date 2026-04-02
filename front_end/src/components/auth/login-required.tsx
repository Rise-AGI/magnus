// front_end/src/components/auth/login-required.tsx
"use client";

import { useState } from "react";
import { Lock, LogIn } from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { IS_LOCAL_MODE } from "@/lib/config";

const MAGNUS_TOKEN_LENGTH = 35;

export function LoginRequired() {
  const { login, loginWithFeishu, loginWithToken } = useAuth();
  const { t } = useLanguage();

  const [showDialog, setShowDialog] = useState(false);
  const [showTokenInput, setShowTokenInput] = useState(false);
  const [tokenValue, setTokenValue] = useState("");
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  const handleTokenLogin = async () => {
    const trimmed = tokenValue.trim();
    if (!trimmed.startsWith("sk-") || trimmed.length !== MAGNUS_TOKEN_LENGTH) {
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
    setShowDialog(false);
    setShowTokenInput(false);
    setTokenValue("");
    setTokenError(null);
  };

  // Local 模式：直接登录，无需弹窗
  if (IS_LOCAL_MODE) {
    return (
      <div className="h-full w-full flex flex-col items-center justify-center min-h-[60vh] text-center">
        <div className="w-16 h-16 bg-zinc-900 rounded-full flex items-center justify-center mb-6 border border-zinc-800 shadow-xl">
          <Lock className="w-8 h-8 text-zinc-500" />
        </div>
        <h2 className="text-xl font-bold text-zinc-200 mb-2">{t("auth.required")}</h2>
        <p className="text-zinc-500 max-w-sm mb-8 text-sm leading-relaxed">
          {t("auth.requiredDesc")} <br />
          {t("auth.pleaseLoginLocal")}
        </p>
        <button
          onClick={login}
          className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-all shadow-lg shadow-blue-900/20 hover:scale-[1.02] active:scale-[0.98]"
        >
          <LogIn className="w-4 h-4" />
          {t("auth.signIn")}
        </button>
      </div>
    );
  }

  // HPC 模式：点击按钮弹出 Dialog，支持飞书登录 + 隐藏的 Token 登录
  return (
    <>
      <div className="h-full w-full flex flex-col items-center justify-center min-h-[60vh] text-center">
        <div className="w-16 h-16 bg-zinc-900 rounded-full flex items-center justify-center mb-6 border border-zinc-800 shadow-xl">
          <Lock className="w-8 h-8 text-zinc-500" />
        </div>
        <h2 className="text-xl font-bold text-zinc-200 mb-2">{t("auth.required")}</h2>
        <p className="text-zinc-500 max-w-sm mb-8 text-sm leading-relaxed">
          {t("auth.requiredDesc")} <br />
          {t("auth.pleaseLogin")}
        </p>
        <button
          onClick={() => setShowDialog(true)}
          className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-all shadow-lg shadow-blue-900/20 hover:scale-[1.02] active:scale-[0.98]"
        >
          <LogIn className="w-4 h-4" />
          {t("auth.signIn")}
        </button>
      </div>

      {showDialog && (
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

              {/* Token 登录输入框 — 点击隐藏按钮后展开 */}
              {showTokenInput && (
                <div className="mt-4 pt-4 border-t border-zinc-800/50">
                  <label className="text-xs text-zinc-500 font-medium block mb-2">
                    {t("auth.tokenLogin")}
                  </label>
                  <input
                    type="text"
                    value={tokenValue}
                    onChange={(e) => { setTokenValue(e.target.value); setTokenError(null); }}
                    placeholder={t("auth.tokenPlaceholder")}
                    maxLength={MAGNUS_TOKEN_LENGTH}
                    className="w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm font-mono text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
                    autoFocus
                    onKeyDown={(e) => { if (e.key === "Enter") handleTokenLogin(); }}
                  />
                  <div className="flex items-center justify-between mt-2">
                    <span className={`text-xs ${tokenValue.length === MAGNUS_TOKEN_LENGTH ? "text-green-500" : "text-zinc-600"}`}>
                      {tokenValue.length}/{MAGNUS_TOKEN_LENGTH}
                    </span>
                    {tokenError && <span className="text-xs text-red-400">{tokenError}</span>}
                  </div>
                  <button
                    onClick={handleTokenLogin}
                    disabled={isLoggingIn || tokenValue.length !== MAGNUS_TOKEN_LENGTH}
                    className="mt-2 w-full px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 border border-blue-500/50 shadow-lg transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isLoggingIn ? "..." : t("auth.tokenLoginButton")}
                  </button>
                </div>
              )}
            </div>

            <div className="bg-zinc-900/50 px-6 py-4 flex items-center justify-between border-t border-zinc-800/50">
              {/* 隐藏的 Token 登录入口 — 默认透明，hover 显示 */}
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
                <button
                  onClick={loginWithFeishu}
                  className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 border border-blue-500/50 shadow-lg shadow-blue-900/20 transition-all flex items-center gap-2"
                >
                  <LogIn className="w-4 h-4" />
                  {t("auth.signInWithFeishu")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

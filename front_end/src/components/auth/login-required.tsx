// front_end/src/components/auth/login-required.tsx
"use client";

import { Lock, LogIn } from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";

export function LoginRequired() {
  const { login } = useAuth();
  const { t } = useLanguage();

  return (
    <div className="h-full w-full flex flex-col items-center justify-center min-h-[60vh] text-center">
      <div className="w-16 h-16 bg-zinc-900 rounded-full flex items-center justify-center mb-6 border border-zinc-800 shadow-xl">
        <Lock className="w-8 h-8 text-zinc-500" />
      </div>

      <h2 className="text-xl font-bold text-zinc-200 mb-2">
        {t("auth.required")}
      </h2>

      <p className="text-zinc-500 max-w-sm mb-8 text-sm leading-relaxed">
        {t("auth.requiredDesc")} <br/>
        {t("auth.pleaseLogin")}
      </p>

      <button
        onClick={login}
        className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-all shadow-lg shadow-blue-900/20 hover:scale-[1.02] active:scale-[0.98]"
      >
        <LogIn className="w-4 h-4" />
        {t("auth.signInWithFeishu")}
      </button>
    </div>
  );
}
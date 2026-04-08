// front_end/src/app/auth/callback/page.tsx
"use client";

import { useEffect, useRef, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { API_BASE, DEFAULT_ROUTE } from "@/lib/config";
import { LoginResponse } from "@/types/auth";
import { Loader2 } from "lucide-react";
import { useLanguage } from "@/context/language-context";

function LoadingState() {
  const { t } = useLanguage();
  return (
    <div className="flex flex-col items-center justify-center h-screen space-y-4">
      <Loader2 className="w-10 h-10 animate-spin text-blue-600" />
      <p className="text-gray-500">{t("auth.authenticating")}</p>
    </div>
  );
}

function AuthCallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const code = searchParams.get("code");
  const oauthError = searchParams.get("error");
  const oauthErrorDesc = searchParams.get("error_description");
  const { t } = useLanguage();

  // 防止 React StrictMode 在开发环境下导致 useEffect 执行两次
  const hasFetched = useRef(false);
  const [error, setError] = useState("");

  useEffect(() => {
    // OAuth 2.0 RFC 6749 §4.1.2.1: 授权服务器通过 error 参数返回错误
    if (oauthError) {
      setError(oauthErrorDesc || t("auth.oauthDenied"));
      return;
    }

    if (!code || hasFetched.current) return;
    
    hasFetched.current = true;

    const doLogin = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/auth/feishu/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code }),
        });

        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || "Login failed");
        }

        const data: LoginResponse = await res.json();
        
        // 存储认证信息
        // TODO: 生产环境建议使用 HttpOnly Cookie 替代 localStorage
        localStorage.setItem("magnus_token", data.access_token);
        localStorage.setItem("magnus_user", JSON.stringify(data.user));

        // 通知 AuthContext 更新状态
        window.dispatchEvent(new Event("magnus-auth-change"));

        router.push(DEFAULT_ROUTE);
        
      } catch (err: any) {
        console.error("Login Error:", err);
        setError(err.message || "Authentication failed");
      }
    };

    doLogin();
  }, [code, router, oauthError, oauthErrorDesc, t]);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-screen text-center bg-[#050505]">
        <div className="w-16 h-16 bg-zinc-900 rounded-full flex items-center justify-center mb-6 border border-zinc-800 shadow-xl">
          <svg xmlns="http://www.w3.org/2000/svg" className="w-8 h-8 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
        </div>
        <h2 className="text-xl font-bold text-zinc-200 mb-2">{t("auth.loginFailed")}</h2>
        <p className="text-zinc-500 max-w-sm mb-8 text-sm leading-relaxed">{error}</p>
        <button
          onClick={() => router.push("/")}
          className="flex items-center gap-2 px-6 py-2.5 bg-zinc-900 hover:bg-zinc-800 text-zinc-300 hover:text-white rounded-lg font-medium transition-all border border-zinc-800 hover:border-zinc-700"
        >
          {t("auth.backToHome")}
        </button>
      </div>
    );
  }

  return <LoadingState />;
}

export default function AuthCallbackPage() {
  return (
    // Suspense 边界是 Next.js App Router 中使用 useSearchParams 的强制要求
    // 否则会导致构建时静态页面生成失败
    <Suspense fallback={<LoadingState />}>
      <AuthCallbackContent />
    </Suspense>
  );
}
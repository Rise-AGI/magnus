// front_end/src/app/auth/callback/page.tsx
"use client";

import { useEffect, useRef, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { API_BASE } from "@/lib/config";
import { LoginResponse } from "@/types/auth";
import { Loader2 } from "lucide-react";

function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center h-screen space-y-4">
      <Loader2 className="w-10 h-10 animate-spin text-blue-600" />
      <p className="text-gray-500">Authenticating with Feishu...</p>
    </div>
  );
}

function AuthCallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const code = searchParams.get("code");
  
  // 防止 React StrictMode 在开发环境下导致 useEffect 执行两次
  const hasFetched = useRef(false);
  const [error, setError] = useState("");

  useEffect(() => {
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

        router.push("/jobs");
        
      } catch (err: any) {
        console.error("Login Error:", err);
        setError(err.message || "Authentication failed");
      }
    };

    doLogin();
  }, [code, router]);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-screen text-red-500">
        <h2 className="text-xl font-bold">Login Failed</h2>
        <p>{error}</p>
        <button 
          onClick={() => router.push("/")}
          className="mt-4 px-4 py-2 bg-gray-200 rounded hover:bg-gray-300 text-black"
        >
          Back to Home
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
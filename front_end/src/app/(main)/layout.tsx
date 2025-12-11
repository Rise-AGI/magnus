// front_end/src/app/(main)/layout.tsx
"use client";

import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { useAuth } from "@/context/auth-context";
import { LoginRequired } from "@/components/auth/login-required";
import { Loader2 } from "lucide-react";

export default function MainLayout({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();

  return (
    <div className="min-h-screen bg-[#050505]">
      {/* 永远显示，包含了登录按钮 */}
      <Sidebar />

      <div className="pl-64">
        {/* Header 永远显示，提供了基础导航 */}
        <Header />

        <main className="p-8">
          {isLoading ? (
            // 状态 A: 加载中
            <div className="h-[60vh] flex items-center justify-center text-zinc-500 gap-2">
              <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
              <span className="text-sm font-medium">Verifying access...</span>
            </div>
          ) : !user ? (
            // 状态 B: 未登录 -> 显示遮罩组件
            <LoginRequired />
          ) : (
            // 状态 C: 已登录 -> 显示真实内容
            <div className="animate-in fade-in duration-500">
               {children}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
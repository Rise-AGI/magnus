// front_end/src/app/(main)/layout.tsx
"use client";

import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { useAuth } from "@/context/auth-context";
import { useLanguage } from "@/context/language-context";
import { LoginRequired } from "@/components/auth/login-required";
import { LoginDialog } from "@/components/auth/login-dialog";
import { Loader2 } from "lucide-react";
import { usePathname } from "next/navigation";

export default function MainLayout({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();
  const { t } = useLanguage();
  const pathname = usePathname();
  const isExplorePage = pathname?.startsWith("/explorer") || pathname?.startsWith("/chat");

  return (
    <>
    <div className="h-dvh w-full bg-[#050505] overflow-hidden flex">
      {/* Sidebar - hidden on mobile */}
      <div className="hidden md:flex flex-shrink-0 w-64 h-full border-r border-zinc-800 bg-[#050505]">
         <Sidebar />
      </div>

      <div className="flex-1 flex flex-col min-w-0 h-full">
        <Header />

        {/* 修改点 3: 确保 main 也是 flex 布局的一部分，且通过 relative 建立层叠上下文 */}
        <main className={isExplorePage ? "flex-1 min-h-0 min-w-0 overflow-hidden relative" : "flex-1 p-4 sm:p-6 lg:p-8 overflow-y-auto"}>
          {isLoading ? (
            <div className="h-full flex items-center justify-center text-zinc-500 gap-2">
              <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
              <span className="text-sm font-medium">{t("auth.verifyingAccess")}</span>
            </div>
          ) : !user ? (
            <LoginRequired />
          ) : (
            <div className={isExplorePage ? "h-full w-full" : "animate-in fade-in duration-500 max-w-[1400px] mx-auto w-full"}>
               {children}
            </div>
          )}
        </main>
      </div>
    </div>
    <LoginDialog />
    </>
  );
}

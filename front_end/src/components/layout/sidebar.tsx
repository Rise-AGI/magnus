// front_end/src/components/layout/sidebar.tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Rocket, Activity, Server, LogIn, LogOut, User as UserIcon } from "lucide-react";
// 👇 引入 Auth Hook
import { useAuth } from "@/context/auth-context"; 

const NAV_ITEMS = [
  { name: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { name: "Jobs", href: "/jobs", icon: Rocket },
  { name: "Cluster", href: "/cluster", icon: Activity },
];

export function Sidebar() {
  const pathname = usePathname();
  // 👇 获取用户状态
  const { user, login, logout, isLoading } = useAuth(); 

  return (
    <aside className="w-64 h-screen fixed left-0 top-0 border-r border-zinc-800 bg-zinc-950/50 backdrop-blur-xl flex flex-col z-50">
      {/* Logo Area */}
      <div className="h-16 flex items-center px-6 border-b border-zinc-800">
        <div className="font-bold text-xl tracking-tighter text-zinc-100">
          Magnus<span className="text-blue-500">Platform</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-6 px-3 space-y-1">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all text-sm font-medium
                ${isActive 
                  ? "bg-blue-600/10 text-blue-400" 
                  : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100"
                }`}
            >
              <item.icon className="w-4 h-4" />
              {item.name}
            </Link>
          );
        })}
      </nav>

      {/* 👇 Auth & Footer Section */}
      <div className="border-t border-zinc-800 bg-zinc-950/30">
        
        {/* User Profile Area */}
        <div className="p-3">
          {isLoading ? (
            // Loading Skeleton
            <div className="h-10 animate-pulse bg-zinc-900 rounded-lg"></div>
          ) : !user ? (
            // State A: Not Logged In
            <button
              onClick={login}
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all text-sm font-medium text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100 hover:shadow-inner"
            >
              <LogIn className="w-4 h-4" />
              Sign in with Feishu
            </button>
          ) : (
            // State B: Logged In
            <div className="flex items-center gap-3 px-3 py-2 rounded-lg bg-zinc-900/50 border border-zinc-800/50">
              {/* Avatar */}
              {user.avatar_url ? (
                <img src={user.avatar_url} alt={user.name} className="w-8 h-8 rounded-full bg-zinc-800" />
              ) : (
                <div className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center text-zinc-400">
                  <UserIcon className="w-4 h-4" />
                </div>
              )}
              
              {/* Name Info */}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-zinc-200 truncate">{user.name}</p>
                <p className="text-xs text-zinc-500 truncate">Researcher</p>
              </div>

              {/* Logout Button */}
              <button 
                onClick={logout}
                className="p-1.5 rounded-md text-zinc-500 hover:text-red-400 hover:bg-red-400/10 transition-colors"
                title="Log out"
              >
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>

        {/* Footer Info (Kept Original) */}
        <div className="px-6 pb-4 pt-1 text-xs text-zinc-600 flex justify-between items-center">
          <span>PKU-Plasma</span>
          <div className="flex items-center gap-2">
            <Server className="w-3 h-3" />
            <span>v0.1.0</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
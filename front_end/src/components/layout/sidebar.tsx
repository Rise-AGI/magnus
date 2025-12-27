// front_end/src/components/layout/sidebar.tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Rocket, Activity, Server, LogIn, LogOut, User as UserIcon, ScrollText } from "lucide-react";
import { useAuth } from "@/context/auth-context"; 
import Image from "next/image";

// 顺序：Dashboard -> Jobs -> Cluster -> Blueprints
const NAV_ITEMS = [
  { name: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { name: "Jobs", href: "/jobs", icon: Rocket },
  { name: "Cluster", href: "/cluster", icon: Activity },
  { name: "Blueprints", href: "/blueprints", icon: ScrollText },
];

export function Sidebar() {
  const pathname = usePathname();
  const { user, login, logout, isLoading } = useAuth(); 

  return (
    <aside className="w-64 h-screen fixed left-0 top-0 border-r border-zinc-800 bg-zinc-950/50 backdrop-blur-xl flex flex-col z-50">
      <div className="h-16 flex items-center justify-between px-8 border-b border-zinc-800 bg-zinc-900/20">
        <div className="font-bold text-xl tracking-tighter text-zinc-100 cursor-default select-none">
          Magnus<span className="text-blue-500">Platform</span>
        </div>
        
          <Image
            src="/api/logo"
            alt="Magnus Logo"
            width={28}
            height={28}
            className="rounded-full object-cover border border-zinc-700/50 shadow-sm opacity-90 hover:opacity-100 transition-opacity"
            unoptimized
          />
      </div>

      <nav className="flex-1 py-6 px-3 space-y-1">
        {NAV_ITEMS.map((item) => {
          // 简单的激活判断逻辑
          const isActive = pathname === item.href || (item.href !== '/dashboard' && pathname.startsWith(item.href));
          
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all text-sm font-medium group
                ${isActive 
                  ? "bg-blue-600/10 text-blue-400 border border-blue-600/10 shadow-[0_0_15px_rgba(37,99,235,0.1)]" 
                  : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100 border border-transparent"
                }`}
            >
              <item.icon className={`w-4 h-4 transition-colors ${isActive ? "text-blue-400" : "text-zinc-500 group-hover:text-zinc-300"}`} />
              {item.name}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-zinc-800 bg-zinc-900/20 p-3 flex flex-col gap-3">
        <div>
          {isLoading ? (
            <div className="h-12 animate-pulse bg-zinc-900 rounded-lg border border-zinc-800"></div>
          ) : !user ? (
            <button
              onClick={login}
              className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg transition-all text-sm font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-300 hover:text-white border border-zinc-800 hover:border-zinc-700"
            >
              <LogIn className="w-4 h-4" />
              <span>Sign in with Feishu</span>
            </button>
          ) : (
            <div className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-zinc-900 border border-zinc-800 shadow-sm">
              <div className="w-9 h-9 rounded-full bg-zinc-800 border border-zinc-700/50 flex-shrink-0 overflow-hidden flex items-center justify-center">
                 {user.avatar_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={user.avatar_url} alt={user.name} className="w-full h-full object-cover" />
                 ) : (
                    <span className="text-xs font-bold text-zinc-400">{user.name.substring(0, 1).toUpperCase()}</span>
                 )}
              </div>
              
              <div className="flex-1 min-w-0 flex flex-col justify-center">
                <p className="text-sm font-semibold text-zinc-200 truncate leading-none mb-1">{user.name}</p>
                <p className="text-[10px] text-zinc-500 truncate font-mono" title={user.email || ""}>
                    {user.email || ""}
                </p>
              </div>

              <button 
                onClick={logout}
                className="p-1.5 rounded-md text-zinc-500 hover:text-red-400 hover:bg-red-400/10 transition-colors flex-shrink-0"
                title="Log out"
              >
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>

        <div className="px-2 pb-1 text-[10px] tracking-wider text-zinc-600 flex justify-between items-center font-medium">
          <span>PKU-PHY</span>
          <div className="flex items-center gap-1.5">
            <Server className="w-3 h-3" />
            <span>v0.1.0</span>
          </div>
        </div>
      </div>
    </aside>
  );
}